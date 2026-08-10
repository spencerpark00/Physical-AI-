"""
D-008 PyBullet 시뮬 - Moby 이동 + 바닥 블레이드 반복 검사

  흐름:
    1) 검사 대상 블레이드가 바닥에 일정 간격으로 놓여 있다
    2) Moby(이동 베이스)가 다음 블레이드 앞으로 이동해 자리를 잡는다
    3) 정지하면 Indy7이 위에서 내려다보며 촬영한다 (뒷면 검사 없음, 놓인 그대로)
    4) 4개를 같은 방식으로 반복

  메시지: 속도가 아니라 '같은 조건으로 지치지 않고 반복' (PM 문서 §5)

  실행:
    py -3.11 sim_blade_moby.py            # 3D 창 미리보기
    py -3.11 sim_blade_moby.py --render   # 렌더 -> frames/*.png
    py -3.11 make_mp4.py                  # -> sim.mp4
"""
import pybullet as p
import pybullet_data
import math, os, sys, time, zlib, struct

def _save_png(rgb, path):
    h, w, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(h))
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 6))
           + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)

ARGS = sys.argv[1:]
RENDER = "--render" in ARGS
HERE = os.path.dirname(os.path.abspath(__file__))
BLADE_OBJ = os.path.join(HERE, "blade", "hpt_blade.obj").replace("\\", "/")

FPS = 30
W, H = (1920, 1080) if RENDER else (1280, 720)

p.connect(p.DIRECT if RENDER else p.GUI, options=f"--width={W} --height={H}")
p.configureDebugVisualizer(p.COV_ENABLE_SHADOWS, 1)
p.setAdditionalSearchPath(pybullet_data.getDataPath())

def box(pos, half, rgba, orn=None):
    v = p.createVisualShape(p.GEOM_BOX, halfExtents=half, rgbaColor=rgba)
    c = p.createCollisionShape(p.GEOM_BOX, halfExtents=half)
    return p.createMultiBody(0, c, v, basePosition=pos, baseOrientation=orn or [0,0,0,1])

def cyl(pos, r, h, rgba, orn=None):
    v = p.createVisualShape(p.GEOM_CYLINDER, radius=r, length=h, rgbaColor=rgba)
    c = p.createCollisionShape(p.GEOM_CYLINDER, radius=r, height=h)
    return p.createMultiBody(0, c, v, basePosition=pos, baseOrientation=orn or [0,0,0,1])

plane = p.loadURDF("plane.urdf")
p.changeVisualShape(plane, -1, rgbaColor=[0.16, 0.18, 0.21, 1], textureUniqueId=-1)

# ── 바닥에 놓인 블레이드 4개 (일정 간격, 눕혀서 거치) ─────────
N_BLADE   = 4
PITCH     = 0.62            # 블레이드 간 간격
BLADE_Y   = 0.68            # Moby 진행선(y=0)에서 블레이드까지 거리
BLADE_SCALE = 0.0159
LAY_ORN = p.getQuaternionFromEuler([math.pi/2, 0, 0])   # 눕히기

def blade_x(i): return (i - (N_BLADE - 1)/2) * PITCH

TRAY, BLADES = [], []
for i in range(N_BLADE):
    bx = blade_x(i)
    # 받침 트레이 (바닥에 깔린 정비 트레이)
    TRAY.append(box([bx, BLADE_Y, 0.008], [0.16, 0.10, 0.008], [0.24, 0.27, 0.33, 1]))
    v = p.createVisualShape(p.GEOM_MESH, fileName=BLADE_OBJ, meshScale=[BLADE_SCALE]*3,
                            rgbaColor=[0.62, 0.65, 0.70, 1], specularColor=[0.45,0.45,0.5])
    BLADES.append(p.createMultiBody(0, -1, v, basePosition=[bx, BLADE_Y, 0.035],
                                    baseOrientation=LAY_ORN))
DONE_COLOR   = [0.36, 0.72, 0.52, 1]   # 검사 완료
ACTIVE_COLOR = [0.30, 0.82, 0.90, 1]   # 지금 촬영 중

# ── Moby (이동 베이스) + Indy7 ──────────────────────────────
MOBY_H = 0.28
MOBY_PARTS = []
def build_moby():
    b = box([0, 0, MOBY_H/2], [0.26, 0.20, MOBY_H/2], [0.20, 0.22, 0.26, 1])
    t = box([0, 0, MOBY_H + 0.012], [0.28, 0.22, 0.012], [0.30, 0.33, 0.38, 1])
    ws = [cyl([sx, sy, 0.07], 0.07, 0.05, [0.08,0.09,0.11,1],
              p.getQuaternionFromEuler([math.pi/2, 0, 0]))
          for sx in (-0.18, 0.18) for sy in (-0.21, 0.21)]
    return [b, t] + ws
MOBY_PARTS = build_moby()
MOBY_LOCAL = [p.getBasePositionAndOrientation(b)[0] for b in MOBY_PARTS]

robot = p.loadURDF("indy7.urdf", basePosition=[0, 0, MOBY_H + 0.024], useFixedBase=True)
EE = 7
MOV = [j for j in range(p.getNumJoints(robot)) if p.getJointInfo(robot, j)[2] == p.JOINT_REVOLUTE]

cam_body  = box([0,0,-5], [0.035, 0.045, 0.028], [0.13, 0.15, 0.18, 1])
lens_body = cyl([0,0,-5], 0.018, 0.03, [0.08, 0.09, 0.11, 1])

# ── 포즈: 위에서 수직으로 내려다보는 촬영 ────────────────────
CAM_H  = 0.42                                   # 블레이드 위 촬영 높이
DOWN   = p.getQuaternionFromEuler([0, math.pi/2, 0])   # 카메라 광축(+X)이 -Z(아래)를 향함
HOME   = [0, math.radians(-100), math.radians(115), 0, math.radians(-10), 0]
_LO=[-3.0]*len(MOV); _HI=[3.0]*len(MOV); _RNG=[6.0]*len(MOV)

def ik_at(rel_x, rel_y, z):
    """Moby 기준 상대좌표로 IK (Moby가 블레이드 앞에 정렬돼 있다고 가정)"""
    for i, j in enumerate(MOV):
        p.resetJointState(robot, j, HOME[i])
    q = p.calculateInverseKinematics(robot, EE, [rel_x, rel_y, z], DOWN,
                                     lowerLimits=_LO, upperLimits=_HI, jointRanges=_RNG,
                                     restPoses=HOME, maxNumIterations=800, residualThreshold=1e-6)
    return list(q[:len(MOV)])

# Moby는 블레이드 정면(y=0 라인)에 서고, 팔은 +Y로 뻗어 블레이드 위를 내려다봄
#   IK가 요청 x를 정확히 못 맞추는 잔차가 있어, 실제 도달 위치를 측정해 목표를 되잡는다
#   (그래야 카메라가 블레이드 '정중앙' 위에 온다 - 4개 모두 동일 조건)
def solve_shoot():
    tx, ty, tz = 0.0, BLADE_Y, 0.03 + CAM_H
    q = ik_at(tx, ty, tz)
    for _ in range(6):                       # 잔차만큼 목표를 반대로 밀어 재수렴
        for i, j in enumerate(MOV):
            p.resetJointState(robot, j, q[i])
        pos = p.getLinkState(robot, EE)[4]
        ex, ey = pos[0] - 0.0, pos[1] - BLADE_Y
        if abs(ex) < 0.003 and abs(ey) < 0.003:
            break
        tx -= ex; ty -= ey
        q = ik_at(tx, ty, tz)
    return q

SHOOT_POSE = solve_shoot()                          # 촬영 자세 (카메라가 블레이드 정중앙 위)
LIFT_POSE  = ik_at(0.0, BLADE_Y*0.55, 0.03 + CAM_H + 0.20)  # 이동 중 접힌 자세

# ── 타임라인 (1.5배 빠르게) ─────────────────────────────────
SPEED   = 1.5
T_DRIVE = 1.6 / SPEED        # Moby 이동
T_REACH = 0.9 / SPEED        # 팔 내리기
T_HOLD  = 0.5 / SPEED        # 정지 = 촬영
T_BACK  = 0.7 / SPEED        # 팔 접기

SEQ = []        # (t0, t1, kind, i)  kind: drive | reach | hold | back
t = 0.4
for i in range(N_BLADE):
    SEQ.append((t, t + T_DRIVE, "drive", i)); t += T_DRIVE
    SEQ.append((t, t + T_REACH, "reach", i)); t += T_REACH
    SEQ.append((t, t + T_HOLD,  "hold",  i)); t += T_HOLD
    SEQ.append((t, t + T_BACK,  "back",  i)); t += T_BACK
SEQ.append((t, t + T_DRIVE, "home", N_BLADE-1)); t += T_DRIVE
DUR = t + 0.4
SHOTS = [s for s, e, k, i in SEQ if k == "hold"]

def ease(x): return x*x*(3 - 2*x)

def state_at(sec):
    """(moby_x, arm_q, done_count) 계산"""
    mx = blade_x(0)
    q  = LIFT_POSE
    done = 0
    for (t0, t1, kind, i) in SEQ:
        if sec >= t1:                       # 지나간 구간 반영
            if kind in ("drive", "home"): mx = blade_x(i) if kind == "drive" else blade_x(0)
            if kind == "hold": done = max(done, i + 1)
            q = LIFT_POSE if kind in ("drive", "back", "home") else SHOOT_POSE
            continue
        if t0 <= sec < t1:
            r = ease((sec - t0)/(t1 - t0))
            if kind == "drive":
                prev = blade_x(i-1) if i > 0 else blade_x(0)
                mx = prev + (blade_x(i) - prev)*r
                q = LIFT_POSE
            elif kind == "home":
                mx = blade_x(i) + (blade_x(0) - blade_x(i))*r
                q = LIFT_POSE
            elif kind == "reach":
                mx = blade_x(i)
                q = [a + (b-a)*r for a, b in zip(LIFT_POSE, SHOOT_POSE)]
            elif kind == "hold":
                mx = blade_x(i); q = SHOOT_POSE
            else:                            # back
                mx = blade_x(i)
                q = [a + (b-a)*r for a, b in zip(SHOOT_POSE, LIFT_POSE)]
            break
    return mx, q, done

def is_flash(sec):
    return any(s <= sec < s + 0.16 for s in SHOTS)

def apply_state(sec):
    mx, q, done = state_at(sec)
    # Moby 본체 이동
    for b, loc in zip(MOBY_PARTS, MOBY_LOCAL):
        pos, orn = p.getBasePositionAndOrientation(b)
        p.resetBasePositionAndOrientation(b, [loc[0] + mx, loc[1], loc[2]], orn)
    # 로봇도 함께 이동 (베이스 위치 재설정)
    p.resetBasePositionAndOrientation(robot, [mx, 0, MOBY_H + 0.024], [0,0,0,1])
    for i, j in enumerate(MOV):
        p.resetJointState(robot, j, q[i])
    # 카메라 헤드
    st = p.getLinkState(robot, EE, computeForwardKinematics=True)
    pos, orn = st[4], st[5]
    R = p.getMatrixFromQuaternion(orn); fwd = [R[0], R[3], R[6]]
    p.resetBasePositionAndOrientation(cam_body, [pos[i] + fwd[i]*0.030 for i in range(3)], orn)
    lens_orn = p.multiplyTransforms([0,0,0], orn, [0,0,0], p.getQuaternionFromEuler([0, math.pi/2, 0]))[1]
    p.resetBasePositionAndOrientation(lens_body, [pos[i] + fwd[i]*0.072 for i in range(3)], lens_orn)
    # 검사 완료 표시
    # 색 상태: 대기(회색) / 촬영중(청록 강조) / 완료(초록)
    cur = None
    for (t0, t1, kind, i) in SEQ:
        if t0 <= sec < t1 and kind in ("reach", "hold", "back"):
            cur = i; break
    for i in range(N_BLADE):
        if i < done and i != cur:   c = DONE_COLOR
        elif i == cur:              c = ACTIVE_COLOR
        else:                       c = [0.62, 0.65, 0.70, 1]
        p.changeVisualShape(BLADES[i], -1, rgbaColor=c)
    return mx

# ── 카메라: 라인 전체가 보이도록 (반복이 눈에 들어와야 함) ────
def view_at(sec):
    yaw = math.radians(-90 + 8*(sec/DUR))
    pitch = math.radians(-22)
    dist = 2.9
    tgt = [0, BLADE_Y*0.5, 0.30]
    eye = [tgt[0] - dist*math.cos(pitch)*math.cos(yaw),
           tgt[1] - dist*math.cos(pitch)*math.sin(yaw),
           tgt[2] - dist*math.sin(pitch)]
    return p.computeViewMatrix(eye, tgt, [0,0,1])

PROJ = p.computeProjectionMatrixFOV(45, W/H, 0.05, 10.0)

# ── 실행 ────────────────────────────────────────────────────
if not RENDER:
    p.resetDebugVisualizerCamera(2.6, -62, -26, [0, BLADE_Y*0.5, 0.30])
    print(f"\n  GUI 미리보기 - {DUR:.1f}초 루프 / 블레이드 {N_BLADE}개 (Moby 이동 + 상방 촬영)")
    print("  창 닫으면 종료\n")
    t0 = time.time()
    while p.isConnected():
        apply_state((time.time() - t0) % DUR)
        p.stepSimulation()
        time.sleep(1/FPS)
else:
    import numpy as np
    out = os.path.join(HERE, "frames")
    os.makedirs(out, exist_ok=True)
    for f in os.listdir(out):
        if f.endswith(".png"): os.remove(os.path.join(out, f))
    total = int(DUR*FPS)
    print(f"\n  렌더 - {total} 프레임 / {DUR:.1f}초 / 촬영 {len(SHOTS)}컷")
    for f in range(total):
        sec = f/FPS
        apply_state(sec)
        _, _, rgb, _, _ = p.getCameraImage(W, H, view_at(sec), PROJ,
                                           renderer=p.ER_BULLET_HARDWARE_OPENGL,
                                           lightDirection=[-0.5, -0.8, 1.2])
        img = np.reshape(np.array(rgb, dtype=np.uint8), (H, W, 4))[:, :, :3]
        if is_flash(sec):
            img = np.clip(img.astype(np.int16)*1.15, 0, 255).astype(np.uint8)
        _save_png(img, os.path.join(out, f"frame_{f:04d}.png"))
        if f % 60 == 0: print(f"    {f}/{total} ({sec:.0f}s)")
    print(f"\n  완료 -> {out}\n  다음:  py -3.11 make_mp4.py\n")
