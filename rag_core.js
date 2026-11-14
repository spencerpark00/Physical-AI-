// ============================================================
// rag_core.js — MRO Copilot 챗봇의 RAG + LLM 핵심 모듈
//   (spencerpark00/-ai- 의 server.js 에서 RAG 관련 구간만 추출·정리)
//
// 구조:  [현장어 정규화] → [Neo4j 지식그래프 근거 검색(RAG)] → [LLM 답변]
//        근거 밖은 답하지 않게 프롬프트로 제약(환각 억제).
//        LLM 키가 없거나 호출 실패 시 → 호출자가 넘긴 규칙 기반 fallback 사용.
//   ※ 임베딩/벡터 검색이 아니라 Neo4j 그래프 조회로 근거를 뽑는 방식이다.
//
// 의존성: Node 18+ 내장 모듈만 (http/https/fs/path). npm install 불필요.
//
// 필요 파일:
//   - 현장용어사전.csv  (term,std,cat,mean,src 5열. 없으면 현장어 정규화만 꺼짐)
//   - Neo4j 에 Part/Defect/Procedure/Step/Tool/SafetyWarning/ExternalCase 노드
//     (seed/d008_blade.cypher, seed/d008_skin.cypher 로 적재)
//
// 환경변수 (.env 또는 셸):
//   NEO4J_URI, NEO4J_USER, NEO4J_PW, NEO4J_DATABASE
//   ANTHROPIC_API_KEY [ANTHROPIC_MODEL]   또는   GEMINI_API_KEY [GEMINI_MODEL, GEMINI_MODELS]
//   LLM_PROVIDER=anthropic|gemini (생략하면 키 있는 쪽 자동 선택)
//
// 사용 예:
//   const rag = require('./rag_core');
//   // 1) 라우트에 바로 붙이기
//   if(u.pathname === '/api/ask') return rag.handleAsk(req, res, { works: WORKS, fallback: myRuleAnswer });
//   // 2) 함수만 쓰기
//   const { part, answer } = await rag.answerLLM('블레이드 검사 절차 알려줘', [], null);
// ============================================================
'use strict';

const http  = require('http');
const https = require('https');
const fs    = require('fs');
const path  = require('path');

// ── .env 로컬 로더 (있으면 읽음) — 무설치, 의존성 없음 ──────
// 이미 설정된 환경변수가 우선 (Render 등 클라우드에선 .env 없이 환경변수로)
try{
  const envPath = path.join(__dirname, '.env');
  if(fs.existsSync(envPath)){
    fs.readFileSync(envPath,'utf8').replace(/^﻿/,'').split(/\r?\n/).forEach(line=>{
      const m = line.match(/^\s*([A-Za-z0-9_]+)\s*=\s*(.*)$/);
      if(m && !process.env[m[1]]) process.env[m[1]] = m[2].trim().replace(/^["']|["']$/g,'');
    });
  }
}catch(e){}

// ── Neo4j 접속 — 환경변수 있으면 클라우드(Aura), 없으면 로컬 ──
const NEO4J_BASE = process.env.NEO4J_URI || 'http://localhost:7474';
const NEO4J_USER = process.env.NEO4J_USER || 'neo4j';
const NEO4J_DB   = process.env.NEO4J_DATABASE || 'neo4j';
const NEO4J_PW   = process.env.NEO4J_PW || 'neo4j';
const NEO4J      = NEO4J_BASE.replace(/\/$/,'') + '/db/' + NEO4J_DB + '/query/v2';
const AUTH       = 'Basic ' + Buffer.from(NEO4J_USER + ':' + NEO4J_PW).toString('base64');
const ROOT       = __dirname;

// ── LLM 설정 ────────────────────────────────────────────────
// 키가 있으면 RAG+LLM, 없으면 규칙 기반 fallback (데모 안전장치).
// provider 자동 감지: ANTHROPIC_API_KEY 있으면 Claude, 아니면 GEMINI_API_KEY로 제미나이.
const GEMINI_KEY     = process.env.GEMINI_API_KEY || '';
const GEMINI_MODEL   = process.env.GEMINI_MODEL || 'gemini-flash-latest';
// 한 모델이 할당량에 걸려도 답이 나가게 — 순서대로 넘어가며 시도한다.
const GEMINI_CHAIN   = (process.env.GEMINI_MODELS
  || [GEMINI_MODEL, 'gemini-2.0-flash', 'gemini-2.5-flash', 'gemini-1.5-flash'].join(','))
  .split(',').map(s=>s.trim()).filter(Boolean)
  .filter((m,i,a)=>a.indexOf(m)===i);
const ANTHROPIC_KEY  = process.env.ANTHROPIC_API_KEY || '';
const ANTHROPIC_MODEL= process.env.ANTHROPIC_MODEL || 'claude-opus-5'; // 비용 아끼려면 claude-haiku-4-5
const LLM_PROVIDER   = process.env.LLM_PROVIDER || (ANTHROPIC_KEY ? 'anthropic' : (GEMINI_KEY ? 'gemini' : 'none'));
const LLM_ON         = LLM_PROVIDER==='anthropic' ? !!ANTHROPIC_KEY
                     : LLM_PROVIDER==='gemini'    ? !!GEMINI_KEY : false;
const LLM_MODEL      = LLM_PROVIDER==='anthropic' ? ANTHROPIC_MODEL : GEMINI_MODEL;

// ── 현장용어사전 로드 (음성/텍스트 현장어 → 표준어) ─────────
// 정비사 VOC + 검색. RAG 근거와 부품 해석 양쪽에 사용.
let FIELD_TERMS = [];
try{
  const raw = fs.readFileSync(path.join(ROOT,'현장용어사전.csv'),'utf8').replace(/^﻿/,'');
  FIELD_TERMS = raw.split(/\r?\n/).slice(1).filter(Boolean).map(line=>{
    const c = line.split(',');
    return {term:c[0], std:c[1], cat:c[2], mean:c[3], src:c[4]};
  }).filter(r=>r.term);
}catch(e){ FIELD_TERMS = []; }

// 질문에 들어있는 현장용어 → 표준어 매핑 목록
function slangHits(q){
  const s = (q||'');
  const seen = new Set(); const out = [];
  for(const r of FIELD_TERMS){
    if(r.term && s.includes(r.term) && !seen.has(r.std)){ seen.add(r.std); out.push(r); }
  }
  return out;
}

// ── 한국어/구어 → 부품명 사전 (자연어 매칭) ────────────────
const SYNONYM = {
  // 엔진 (ATA 72) — D-008 대표 시나리오: 오버홀 중 분해된 HPC 블레이드 외관검사
  '블레이드':'BLADE','blade':'BLADE','에어포일':'BLADE','airfoil':'BLADE','날개깃':'BLADE',
  '베인':'VANE','vane':'VANE','정익':'VANE',
  // 기체 구조 (ATA 53)
  '외판':'SKIN','스킨':'SKIN','skin':'SKIN','표피':'SKIN','판':'SKIN',
  '스트링거':'STRINGER','stringer':'STRINGER','세로재':'STRINGER',
  '프레임':'FRAME','frame':'FRAME','골조':'FRAME',
  '트랙':'TRACK','track':'TRACK','좌석레일':'TRACK','시트트랙':'TRACK','레일':'TRACK',
  '빔':'BEAM','beam':'BEAM','플로어빔':'BEAM','바닥보':'BEAM',
  '패널':'PANEL','panel':'PANEL','플로어패널':'PANEL',
  '앵글':'ANGLE','angle':'ANGLE','ㄱ형강':'ANGLE',
  '웹':'WEB','web':'WEB',
  '서포트':'SUPPORT','support':'SUPPORT','지지대':'SUPPORT','받침':'SUPPORT',
  '브래킷':'BRACKET','bracket':'BRACKET','브라켓':'BRACKET',
  '피팅':'FITTING','fitting':'FITTING','연결구':'FITTING',
  '힌지':'HINGE','hinge':'HINGE','경첩':'HINGE',
  '실':'SILL','sill':'SILL','도어실':'SILL',
  '더블러':'DOUBLER','doubler':'DOUBLER','보강판':'DOUBLER',
  '채널':'CHANNEL','channel':'CHANNEL',
  '거싯':'GUSSET','gusset':'GUSSET',
  '인터코스탈':'INTERCOSTAL','intercostal':'INTERCOSTAL',
  '스티프너':'STIFFENER','stiffener':'STIFFENER','보강재':'STIFFENER',
  '벌크헤드':'BARRIER','격벽':'BARRIER',
  '데크':'DECK','deck':'DECK','프레셔데크':'DECK',
};

// 결함 코드 → 한국어 (근거 텍스트 표기용)
const KORD = {DAMAGED:'손상',CORRODED:'부식',CRACKED:'균열',DENTED:'찍힘',GOUGED:'긁힘',PUNCTURED:'천공',
              WORN:'마모',BENT:'휨',BROKEN:'파손',LOOSE:'풀림',MISSING:'결품',DELAMINATED:'층간분리'};

// ── Neo4j HTTP Query API 호출 ──────────────────────────────
function cypher(statement, parameters){
  return new Promise((resolve, reject) => {
    const body = JSON.stringify({ statement, parameters: parameters || {} });
    const lib = NEO4J.startsWith('https') ? https : http;
    const req = lib.request(NEO4J, {
      method:'POST',
      headers:{ 'Authorization':AUTH, 'Content-Type':'application/json',
                'Accept':'application/json', 'Content-Length':Buffer.byteLength(body) }
    }, res => {
      res.setEncoding('utf8');      // 청크 경계에서 한글이 잘리지 않게
      let d=''; res.on('data',c=>d+=c);
      res.on('end',()=>{
        try{
          const j = JSON.parse(d);
          if(j.errors && j.errors.length) return reject(new Error(j.errors[0].message));
          const fields = (j.data && j.data.fields) || [];
          const rows = ((j.data && j.data.values) || []).map(v=>{
            const o={}; fields.forEach((f,i)=>o[f]=v[i]); return o;
          });
          resolve(rows);
        }catch(e){ reject(new Error('Neo4j 응답 파싱 실패: '+d.slice(0,200))); }
      });
    });
    req.on('error', reject);
    req.write(body); req.end();
  });
}

// ── 검색어 → 부품명 해석 ───────────────────────────────────
async function resolvePart(term){
  const raw = (term||'').trim();
  const low = raw.toLowerCase();
  // 1) 사전 정확/부분 매칭
  for(const k of Object.keys(SYNONYM)){
    if(low.includes(k.toLowerCase())) return SYNONYM[k];
  }
  // 2) 영문 토큰이 부품명과 부분일치
  const parts = await cypher('MATCH (p:Part) RETURN p.name AS name, p.caseCount AS c ORDER BY c DESC', {});
  const upper = raw.toUpperCase();
  const hit = parts.find(p => upper.includes(p.name) || p.name.includes(upper));
  if(hit) return hit.name;
  return null;
}

// ── 부품 지식 조회 (결함 통계 + 과거 사례) ─────────────────
const Q_PART = `
MATCH (p:Part) WHERE toUpper(p.name) = toUpper($part)
OPTIONAL MATCH (p)-[ps:PART_OF]->(s:System)
WITH p, s ORDER BY ps.weight DESC
WITH p, head(collect(s)) AS sys
OPTIONAL MATCH (d:Defect)-[r:OCCURS_ON]->(p)
WITH p, sys, d, r ORDER BY r.weight DESC
WITH p, sys, collect({name:d.name, weight:r.weight}) AS defects
OPTIONAL MATCH (c:ExternalCase {partName:p.name}) WHERE c.summary IS NOT NULL
WITH p, sys, defects, collect({ac:c.aircraft, cond:c.condition, fs:c.fsFrom, str:c.stringer, sum:c.summary}) AS cs
RETURN p.name AS part, p.caseCount AS total, p.confidence AS conf, p.isGeneric AS generic,
       sys.code AS sysCode, sys.name AS sysName, defects, cs[..3] AS cases`;

// ── 부품의 정비절차·공구·주의사항 (FAA 핸드북 지식) ──
// 절차 기준으로 묶어 중복 제거. 어떤 결함에 적용되는지는 defects 로 유지.
const Q_PROCEDURE = `
MATCH (p:Part) WHERE toUpper(p.name)=toUpper($part)
MATCH (d:Defect)-[:OCCURS_ON]->(p)
MATCH (d)-[:REQUIRES_PROCEDURE]->(pr:Procedure)
MATCH (pr)-[:TARGETS]->(p)
// Defect 노드는 이름으로만 식별되어 계통 간 공유된다(CORRODED 는 블레이드에도 외판에도 붙는다).
// 결함만 타고 가면 다른 계통 절차까지 나오므로, 절차가 이 부품을 대상으로 하는지 확인한다.
WITH pr, collect(DISTINCT d.name) AS defects
OPTIONAL MATCH (pr)-[:HAS_STEP]->(st:Step)
WITH pr, defects, st ORDER BY st.seq
WITH pr, defects, collect(DISTINCT st.name) AS steps
OPTIONAL MATCH (pr)-[:REQUIRES_TOOL]->(t:Tool)
WITH pr, defects, steps, collect(DISTINCT t.name) AS tools
OPTIONAL MATCH (pr)-[:HAS_WARNING]->(w:SafetyWarning)
OPTIONAL MATCH (pr)-[:SOURCED_FROM]->(doc:Document)
WITH pr, defects, steps, tools,
     collect(DISTINCT {text:w.text, type:w.type, src:w.sourcePage}) AS warnings,
     head(collect(doc.title)) AS source
RETURN pr.name AS procedure, pr.nameEn AS procedureEn, pr.description AS desc,
       pr.sourcePage AS page, defects, steps, tools, warnings, source
ORDER BY pr.id`;

// ============================================================
// LLM + RAG — 자유 질문·추론·현장어 대응
//   [현장어 정규화] → [Neo4j·교범 근거 검색(RAG)] → [LLM 답변]
//   근거 밖은 답하지 않게 제약(환각 억제). 실패하면 규칙 기반으로 fallback.
// ============================================================
const SYS_PROMPT = `당신은 'MRO Copilot', 항공정비 현장에서 정비사를 돕는 음성·텍스트 도우미입니다. 옆에서 같이 일하는 선배 정비사처럼, 자연스럽고 편안한 한국어 대화체로 답하세요.

[대화 방식]
- 근거를 그대로 베껴 나열하지 말고, 이해한 내용을 자연스러운 문장으로 풀어 설명하세요. 절차처럼 단계가 중요할 땐 짧은 목록을 쓰되, 앞뒤를 말로 이어 붙이세요.
- 이건 하나로 이어지는 대화입니다. 앞선 대화를 기억하고 맥락을 이어받아 답하세요. "그건", "아까 그거", "그럼?" 같은 말도 이전 흐름으로 이해하세요. 같은 말을 반복하지 말고 이어서 대답하세요.
- 핵심부터 말하고 너무 길지 않게. 보통 2~4문장, 절차 설명은 조금 더 길어도 됩니다.

[무엇을 어떻게 답할지 — 질문을 세 종류로 나눠 판단하세요]

① 이 작업·이 부품에 대한 질문 (검사 절차, 결함 이력, 과거 사례, 주의사항, 지금 필요한 공구)
   → 아래 [근거]에 있는 내용으로만 답하세요. 근거에 없으면 "그건 제 자료엔 없어요"라고 말하고
     대신 무엇을 알려줄 수 있는지 제안하세요.

② 정비 일반 상식 (공구가 무엇인지, 용어의 뜻, 장비의 용도, 결함 유형의 일반적 성격)
   → 근거에 없더라도 아는 대로 설명하세요. 이건 지어내는 것이 아니라 상식입니다.
     예: "토크 렌치가 뭐야?" → 규정 토크로 조이는 공구라고 설명.
     설명 끝에 이 작업의 근거와 연결해 주고, 규격·수치가 걸린 부분은
     "정확한 값은 해당 엔진 정비교범(EM/AMM)을 보셔야 합니다"라고 덧붙이세요.

③ 허용한도·수치·합격 기준 (균열 길이 몇 mm까지 허용인지 등)
   → 값을 말하지 마세요. 교범 소관입니다. "교범(EM/AMM) 기준을 확인하셔야 합니다"라고 안내하세요.

[근거가 서로 다를 때]
- 같은 항목(공구, 촬영 횟수 등)이 여러 근거에 나오면 '[이 작업의 …]' 또는 '[로봇 촬영 실적]' 처럼
  지금 작업에 대한 근거를 먼저 믿으세요. '[교범 절차의 참고 공구]'는 일반 절차이지 이 작업의 확정 목록이 아닙니다.
- '요구컷 3장'은 한 번 촬영할 때 찍는 장수이지, 로봇이 몇 번 촬영했는지가 아닙니다. 둘을 섞지 마세요.

[지켜야 할 것]
- 최종 결함 판정은 정비사가 합니다. 당신은 판정하지 않고 정보만 제공하세요.
- 현장용어(메가네·야마·복스 등)로 물어도 이해하고, 필요하면 표준용어를 함께 알려주세요.
- 이름을 물으면 'MRO Copilot'이라고 답하고, 무엇을 할 수 있냐고 물으면: 부품 결함 이력·검사 절차·필요 공구·주의사항·과거 사례 안내, 로봇 촬영 결과 정리, 작업기록 초안 도움 을 한다고 답하세요.`;

// Gemini generateContent 호출 (Node 내장 https만) — model 은 GEMINI_CHAIN 에서 넘어온다
function geminiCall(model, system, turns){
  return new Promise((resolve, reject)=>{
    const contents = turns.map(t=>({ role: t.role==='model'?'model':'user', parts:[{text:t.text}] }));
    const payload = JSON.stringify({
      system_instruction:{ parts:[{text:system}] },
      contents,
      // gemini-flash-latest는 thinking 모델 → 생각이 토큰을 먹어 답이 잘림.
      // 생각 끄기(thinkingBudget:0)는 이 모델에서 빈 응답 → 대신 여유를 넉넉히 준다.
      generationConfig:{ temperature:0.6, maxOutputTokens:8192 }
    });
    const req = https.request({
      method:'POST',
      hostname:'generativelanguage.googleapis.com',
      path:'/v1beta/models/'+model+':generateContent?key='+GEMINI_KEY,
      headers:{ 'Content-Type':'application/json', 'Content-Length':Buffer.byteLength(payload) }
    }, r=>{
      r.setEncoding('utf8');
      let d=''; r.on('data',c=>d+=c);
      r.on('end',()=>{
        try{
          const j = JSON.parse(d);
          if(j.error) return reject(new Error(j.error.message||'gemini error'));
          const cand = (j.candidates||[])[0]||{};
          const parts = (cand.content||{}).parts || [];
          const text = (parts.find(p=>p && p.text)||{}).text || '';  // 생각 파트 건너뛰고 텍스트만
          if(!text) return reject(new Error('빈 응답'));
          resolve(text.trim());
        }catch(e){ reject(new Error('Gemini 파싱 실패: '+d.slice(0,150))); }
      });
    });
    req.on('error', reject);
    req.write(payload); req.end();
  });
}

// 모델 체인을 순서대로 시도 — 하나가 할당량·빈 응답으로 막혀도 다음 모델이 답한다.
async function geminiComplete(system, turns){
  const fails = [];
  for(const model of GEMINI_CHAIN){
    try{
      const text = await geminiCall(model, system, turns);
      if(model !== GEMINI_CHAIN[0]) console.log('  · LLM 대체모델 사용: '+model);
      return text;
    }catch(e){
      fails.push(model+': '+e.message);
      console.log('  ! '+model+' 실패('+e.message+')');
    }
  }
  throw new Error(fails.join(' | ') || 'LLM 실패');
}

// Claude (Anthropic Messages API) 호출 (Node 내장 https만)
function anthropicComplete(system, turns){
  return new Promise((resolve, reject)=>{
    const messages = turns.map(t=>({ role: t.role==='model'?'assistant':'user', content: t.text }));
    const payload = JSON.stringify({
      model: ANTHROPIC_MODEL,
      max_tokens: 2000,
      system,
      messages
    });
    const req = https.request({
      method:'POST',
      hostname:'api.anthropic.com',
      path:'/v1/messages',
      headers:{ 'x-api-key':ANTHROPIC_KEY, 'anthropic-version':'2023-06-01',
                'content-type':'application/json', 'content-length':Buffer.byteLength(payload) }
    }, r=>{
      r.setEncoding('utf8');
      let d=''; r.on('data',c=>d+=c);
      r.on('end',()=>{
        try{
          const j = JSON.parse(d);
          if(j.type==='error' || j.error) return reject(new Error((j.error&&j.error.message)||'anthropic error'));
          if(j.stop_reason==='refusal') return reject(new Error('안전상 거부됨'));
          const blk = (j.content||[]).find(b=>b.type==='text') || {};
          const text = blk.text || '';
          if(!text) return reject(new Error('빈 응답'));
          resolve(text.trim());
        }catch(e){ reject(new Error('Anthropic 파싱 실패: '+d.slice(0,150))); }
      });
    });
    req.on('error', reject);
    req.write(payload); req.end();
  });
}

// provider 디스패처 — answerLLM은 이것만 부른다 (교체 지점 일원화)
function llmComplete(system, turns){
  return LLM_PROVIDER==='anthropic' ? anthropicComplete(system, turns)
                                    : geminiComplete(system, turns);
}

// ── RAG 핵심: 질문에서 부품·현장어를 뽑아 Neo4j·교범 근거를 텍스트로 조립 ──
//   q     : 질문 문자열
//   wctx  : 화면이 보내온 '지금 작업' 컨텍스트 {id, defect, next, acts, tools} (없으면 null)
//   works : 서버의 작업 목록 배열 (wctx.id 로 찾아 로봇 촬영 실적 등을 붙임. 없으면 [])
async function buildContext(q, wctx, works){
  const WORKS = Array.isArray(works) ? works : [];
  const hits = slangHits(q);
  // Neo4j 죽어있어도 LLM은 호출되게 — 각 조회를 개별 try/catch로 감쌈
  let part='BLADE', data=null, procs=[];
  try{ part = await resolvePart(q) || 'BLADE'; }catch(e){}
  try{ data = (await cypher(Q_PART, {part}))[0] || null; }catch(e){}
  try{ procs = await cypher(Q_PROCEDURE, {part}); }catch(e){}
  const L=[];
  if(data){
    L.push(`[부품] ${data.part} — 결함 보고 총 ${data.total||0}건`);
    const defs=(data.defects||[]).filter(x=>x.name).slice(0,5).map(d=>`${KORD[d.name]||d.name} ${d.weight}건`).join(', ');
    if(defs) L.push(`[결함 통계] ${defs}`);
    const cs=(data.cases||[]).filter(c=>c.sum)[0];
    if(cs) L.push(`[과거 사례] "${(cs.sum||'').slice(0,120)}" (${cs.ac||''}${cs.fs?' · FR'+cs.fs:''})`);
  }
  const p=procs[0];
  if(p && (p.steps||[]).length) L.push(`[검사 절차] ${p.procedure}: `+p.steps.map((s,i)=>`${i+1})${s}`).join(' '));
  const tools=[...new Set([].concat(...procs.map(x=>x.tools||[])))];
  if(tools.length) L.push(`[교범 절차의 참고 공구] ${tools.join(', ')}`);
  const warns=[...new Map([].concat(...procs.map(x=>x.warnings||[])).filter(w=>w&&w.text).map(w=>[w.text,w])).values()].slice(0,4);
  if(warns.length) L.push(`[주의사항] `+warns.map((w,i)=>`${i+1})${w.text}`).join(' '));
  if(hits.length) L.push(`[현장용어] `+hits.map(h=>`'${h.term}'=${h.std}`).join(', '));
  // 정비사가 지금 보고 있는 작업 — 화면과 챗봇이 같은 것을 보게 한다.
  //   (이게 없으면 화면에 뜬 공구를 챗봇이 "없다"고 답한다)
  if(wctx && (wctx.id || wctx.defect)){
    const w = WORKS.find(x=>x.id===wctx.id);
    L.push(`[지금 작업] ${wctx.id||''} ${w?w.ac:''} ${w?w.area:''} — ${wctx.defect||(w&&w.defect)||''} 판정 대기`);
    if(wctx.next) L.push(`[다음 조치] ${wctx.next}`);
    if(Array.isArray(wctx.acts) && wctx.acts.length)
      L.push(`[조치 순서] ` + wctx.acts.map((a,i)=>`${i+1})${a}`).join(' '));
    if(Array.isArray(wctx.tools) && wctx.tools.length)
      L.push(`[이 작업의 필요 공구 — 정비사 화면에 표시된 확정 목록] ${wctx.tools.join(', ')}`);
    if(w && w.qc && w.qc.attempts && w.qc.attempts.length){
      const last = w.qc.attempts[w.qc.attempts.length-1];
      const fa   = w.qc.attempts.find(a=>a.result==='FAIL');
      L.push(`[로봇 촬영 실적] 이 블레이드는 로봇이 ${w.qc.shots}회 촬영했고 `
        + `최종 사진에 담긴 비율은 ${Math.round(last.coverage*100)}% 입니다`
        + (fa ? ` (1차 ${Math.round(fa.coverage*100)}% 불합격 — ${fa.reason} → ${fa.replan?fa.replan.detail:'자세 재계산'} 후 재촬영)` : ''));
    }
  }
  return { part, context:L.join('\n') };
}

// ── LLM 경로: 근거 조립 → LLM → 답변 + 이어가기 칩 ──────────
//   history : [{role:'user'|'bot', text}] 최근 대화 (최대 6턴만 사용)
async function answerLLM(q, history, wctx, works){
  const ctx = await buildContext(q, wctx, works);
  const userMsg = `[근거]\n${ctx.context || '(관련 근거 없음)'}\n\n[질문]\n${q}`;
  const turns = [];
  (Array.isArray(history)?history:[]).slice(-6).forEach(h=>{
    const t = (h && h.text || '').trim(); if(!t) return;
    turns.push({ role: h.role==='bot' ? 'model' : 'user', text: t });
  });
  turns.push({ role:'user', text: userMsg });
  while(turns.length && turns[0].role!=='user') turns.shift();  // 첫 턴은 user로 시작
  const text = await llmComplete(SYS_PROMPT, turns);
  return { part:ctx.part, answer:{ text, chips:['검사 절차','필요 공구','주의사항','과거 사례'],
    data:{ source:'FAA-H-8083-31B · Neo4j (RAG)' } } };
}

// ── HTTP 헬퍼 ───────────────────────────────────────────────
function json(res, obj, code){
  res.writeHead(code||200, {'Content-Type':'application/json; charset=utf-8'});
  res.end(JSON.stringify(obj));
}
function readBody(req){
  return new Promise((resolve)=>{
    req.setEncoding('utf8');
    let d=''; req.on('data',c=>{ d+=c; if(d.length>1e6) d=d.slice(0,1e6); });
    req.on('end',()=>resolve(d)); req.on('error',()=>resolve(''));
  });
}

// ── /api/ask 라우트 핸들러 ──────────────────────────────────
//   GET  /api/ask?q=...
//   POST /api/ask  {q, history:[{role,text}], ctx:{id,defect,next,acts,tools}}
//   opts.works    : 서버의 작업 목록 (buildContext 에 전달)
//   opts.fallback : async (q, part) => answer   — LLM 꺼져있거나 실패했을 때 쓸 규칙 기반 답변
//   응답: {q, part, mode:'llm'|'rule', llmError, answer:{text, chips, data}}
async function handleAsk(req, res, opts){
  opts = opts || {};
  let q='', history=[], wctx=null;
  if(req.method === 'POST'){
    try{ const p = JSON.parse(await readBody(req) || '{}'); q = p.q || ''; history = p.history || []; wctx = p.ctx || null; }catch(e){}
  } else {
    const u = new URL(req.url, 'http://localhost');
    q = u.searchParams.get('q') || '';
  }
  try{
    // LLM 켜져 있으면 RAG+LLM 우선. 실패하면 규칙 기반으로 자동 fallback.
    //   실패 사유를 llmError 로 함께 내보내 화면에서 확인할 수 있게 한다.
    let llmErr = null;
    if(LLM_ON){
      try{
        const out = await answerLLM(q, history, wctx, opts.works);
        return json(res, {q, part:out.part, mode:'llm', answer:out.answer});
      }catch(e){
        llmErr = e.message;
        console.log('  ! LLM fallback('+e.message+') → 규칙 기반');
      }
    }
    let part='BLADE';
    try{ part = await resolvePart(q) || 'BLADE'; }catch(e){}
    const answer = typeof opts.fallback === 'function'
      ? await opts.fallback(q, part)
      : { text: LLM_ON ? '지금은 답변을 만들지 못했어요. 잠시 후 다시 물어봐 주세요.'
                       : 'LLM 키가 설정되지 않았습니다. ANTHROPIC_API_KEY 또는 GEMINI_API_KEY 를 넣어주세요.',
          chips:['검사 절차','필요 공구','주의사항','과거 사례'], data:{source:'rule'} };
    return json(res, {q, part, mode:'rule', llmError:llmErr, answer});
  }catch(e){ return json(res, {error:e.message}, 500); }
}

// ── 시작 로그 (server.js 의 연결 점검과 같은 정보) ───────────
function printStatus(){
  console.log('  · Neo4j: '+NEO4J_BASE+' / db:'+NEO4J_DB);
  console.log('  · LLM  : '+(LLM_ON ? LLM_PROVIDER+' ('+LLM_MODEL+')' : '꺼짐 → 규칙 기반 fallback'));
  console.log('  · 현장용어사전 '+FIELD_TERMS.length+'개 로드');
}

module.exports = {
  // 설정 상태
  LLM_ON, LLM_PROVIDER, LLM_MODEL, GEMINI_CHAIN, FIELD_TERMS, SYNONYM, KORD,
  // 쿼리
  Q_PART, Q_PROCEDURE, SYS_PROMPT,
  // 함수
  cypher, slangHits, resolvePart, buildContext, answerLLM,
  llmComplete, geminiComplete, anthropicComplete,
  handleAsk, json, readBody, printStatus,
};

// ============================================================
//    [참고] 프론트(index.html)에서 /api/ask 를 부르는 부분
//    — 대화 이력 6턴 + 지금 작업 컨텍스트를 함께 보내고, LLM 의 **굵게** 를 <b>로 바꿔 그린다.
//
// let chatHist = [], lastPart = 'BLADE';
//
// async function askServer(term){
//   // 지금 보고 있는 작업을 같이 보낸다 — 화면에 뜬 공구·조치를 챗봇도 알아야 한다
//   const askCtx = CURWORK ? {
//     id: CURWORK.id, defect: CURWORK.defect,
//     next: _c ? _c.next : null, acts: _c ? _c.acts : null,
//     tools: _c ? (_c.tools||[]).map(t=>t[0]) : null
//   } : null;
//   const r = await fetch('/api/ask',{method:'POST',headers:{'Content-Type':'application/json'},
//     body:JSON.stringify({q:term, history:chatHist, ctx:askCtx})});
//   const j = await r.json(); const ans = j.answer||{};
//   lastPart = j.part||lastPart;
//   document.getElementById('pending').outerHTML = renderBot(ans);
//   chatHist.push({role:'user',text:term}); chatHist.push({role:'bot',text:ans.text||''});
//   if(chatHist.length>12) chatHist = chatHist.slice(-12);   // 최근 6턴만 유지
// }
//
// function renderBot(a){
//   // 먼저 HTML 특수문자를 막고, 그다음 **굵게** 만 태그로 바꾼다.
//   const text=(a.text||'')
//     .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
//     .replace(/\*\*(.+?)\*\*/g,'<b>$1</b>')
//     .replace(/\n/g,'<br>');
//   const chips=(a.chips||[]).map(c=>'<span class="rchip" onclick="askServer(\''+c+' '+lastPart+'\')">'+c+'</span>').join('');
//   return '<div class="botmsg">'+text+(chips?'<div class="rchips">'+chips+'</div>':'')+'</div>';
// }
// ============================================================
//