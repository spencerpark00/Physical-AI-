"""
D-008 PyBullet 시뮬레이션 - HPC 블레이드 '반복 검사' (랙 다수 순차)

  증명하는 것 (PM 문서 §5 프레임):
    "1장은 사람이 빠르다. 하지만 엔진 1대에 블레이드는 수백 장이고,
     로봇은 수백 장을 '같은 조건으로' 지치지 않고 반복해 기록으로 남긴다."

  - 사람 검사 정확도는 조건에 따라 67~87%로 흔들리고 검사자 간 편차가 큼 (Aust & Pons)
  - 로봇의 강점 = 속도가 아니라 '일관성.가용성.무편향성'
  -> 영상은 '빠름'이 아니라 '동일 동작의 반복'을 보여준다

  실행:
    py -3.11 sim_blade_rack.py            # 3D 창 미리보기
    py -3.11 sim_blade_rack.py --render   # 오프스크린 렌더 -> frames/*.png
    py -3.11 make_mp4.py                  # -> sim.mp4

  ※ 영상에 자막.라벨.bbox 없음 (데모 HTML이 오버레이 담당)
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
    return p.createMultiBody(0, c, v, basePosition=pos, baseOrientation=orn or [0, 0, 0, 1])

def cyl(pos, r, h, rgba, orn=None):
    v = p.createVisualShape(p.GEOM_CYLINDER, radius=r, length=h, rgbaColor=rgba)
    c = p.createCollisionShape(p.GEOM_CYLINDER, radius=r, height=h)
    return p.createMultiBody(0, c, v, basePosition=pos, baseOrientation=orn or [0, 0, 0, 1])

# ── 씬 ──────────────────────────────────────────────────────
plane = p.loadURDF("plane.urdf")
p.changeVisualShape(plane, -1, rgbaColor=[0.16, 0.18, 0.21, 1], textureUniqueId=-1)

# 로봇: 페데스탈 위 Indy7
PED_H = 0.62
cyl([0, 0, PED_H/2], 0.13, PED_H, [0.20, 0.22, 0.26, 1])
robot = p.loadURDF("indy7.urdf", basePosition=[0, 0, PED_H], useFixedBase=True)
EE = 7
MOV = [j for j in range(p.getNumJoints(robot)) if p.getJointInfo(robot, j)[2] == p.JOINT_REVOLUTE]

cam_body = box([0, 0, -5], [0.035, 0.045, 0.028], [0.13, 0.15, 0.18, 1])
lens_body = cyl([0, 0, -5], 0.018, 0.03, [0.08, 0.09, 0.11, 1])

# ── 블레이드 랙: 호(arc) 배치 - 로봇 기준 등거리라 전부 도달 가능 ──
N_BLADE = 6                 # 화면에 보이는 장수 (실제는 엔진당 수백 장)
R_RACK  = 0.55              # 로봇 원점 ~ 블레이드 거리
Z_BLADE = 1.35              # 촬영 타깃 높이
STEP_DEG = 12               # 블레이드 간 각도
D = 0.18                    # 작동거리 (IndyEye 5~70cm)
BLADE_SCALE = 0.0159        # 실제 21.9cm

def arc_xy(i):
    a = math.radians((i - (N_BLADE - 1)/2) * STEP_DEG)
    return R_RACK*math.cos(a), R_RACK*math.sin(a), a

# 랙 프레임 (얇은 곡선 빔 + 다리) - 팔이 지나갈 공간 확보
RACK_Z = Z_BLADE - 0.30
#   지주는 로봇 팔 경로(z 0.9~1.1)를 피해 '뒤쪽으로 물러난' 공통 빔에서 지지한다
#   각 블레이드는 '아래에서 올라오는 얇은 기둥' 하나로만 지지 -> 팔 경로(옆.위)가 완전히 비워짐
POST_R = 0.010
for i in range(N_BLADE):
    bx, by, a = arc_xy(i)
    cyl([bx, by, (RACK_Z - 0.06)/2], POST_R, RACK_Z - 0.06, [0.19, 0.22, 0.27, 1])   # 지지 기둥
    cyl([bx, by, 0.008], 0.055, 0.016, [0.24, 0.27, 0.33, 1])                        # 바닥 베이스

# 블레이드 본체 (검사 완료 시 색이 바뀌도록 id 보관)
BLADES = []
for i in range(N_BLADE):
    bx, by, a = arc_xy(i)
    v = p.createVisualShape(p.GEOM_MESH, fileName=BLADE_OBJ, meshScale=[BLADE_SCALE]*3,
                            rgbaColor=[0.62, 0.65, 0.70, 1], specularColor=[0.45, 0.45, 0.5])
    b = p.createMultiBody(0, -1, v, basePosition=[bx, by, RACK_Z - 0.035],
                          baseOrientation=p.getQuaternionFromEuler([0, 0, a]))
    BLADES.append(b)

DONE_COLOR = [0.36, 0.72, 0.52, 1]     # 검사 완료 표시(연출) - 진행이 눈에 보이게

# ── 포즈 ────────────────────────────────────────────────────
HOME = [0, math.radians(-100), math.radians(115), 0, math.radians(-10), 0]
_LO = [-3.0]*len(MOV); _HI = [3.0]*len(MOV); _RNG = [6.0]*len(MOV)

def look_orn(eye, ctr):
    dx, dy, dz = (ctr[i] - eye[i] for i in range(3))
    return p.getQuaternionFromEuler([0, -math.atan2(dz, math.hypot(dx, dy)), math.atan2(dy, dx)])

def ik(target, ctr):
    for i, j in enumerate(MOV):
        p.resetJointState(robot, j, HOME[i])
    q = p.calculateInverseKinematics(robot, EE, target, look_orn(target, ctr),
                                     lowerLimits=_LO, upperLimits=_HI, jointRanges=_RNG,
                                     restPoses=HOME, maxNumIterations=600, residualThreshold=1e-5)
    return list(q[:len(MOV)])

def poses_for(i):
    """블레이드 i의 3면 촬영 포즈 + 위쪽 경유 포즈"""
    bx, by, a = arc_xy(i)
    ctr = [bx, by, Z_BLADE]
    lead = [(R_RACK - D)*math.cos(a), (R_RACK - D)*math.sin(a), Z_BLADE]     # 리딩엣지
    pres = [bx + D*math.sin(a), by - D*math.cos(a), Z_BLADE]                 # 압력면
    suct = [bx - D*math.sin(a), by + D*math.cos(a), Z_BLADE]                 # 흡입면
    way  = [(R_RACK - D*1.2)*math.cos(a), (R_RACK - D*1.2)*math.sin(a), Z_BLADE + 0.26]
    return (ik(lead, ctr), ik(pres, ctr), ik(suct, ctr), ik(way, ctr))

POSE = [poses_for(i) for i in range(N_BLADE)]

# ── 타임라인: 블레이드당 3면 촬영, 순차 반복 ──────────────────
#   블레이드 1장 = 4.0초 (경유0.7 + 3면 x (이동0.7 + 정지0.4))
T_WAY, T_MOVE, T_HOLD = 0.7, 0.7, 0.4
KEY, SHOTS, DONE_AT = [], [], []
t = 0.0
KEY.append((t, HOME)); t += 1.0                       # 대기
for i in range(N_BLADE):
    lead, pres, suct, way = POSE[i]
    KEY.append((t, way)); t += T_WAY                  # 위로 경유(다음 블레이드로 우회)
    for q in (lead, pres, suct):                      # 3면 = 요구컷
        t += T_MOVE; KEY.append((t, q))
        SHOTS.append(t)                               # 정지 시작 = 촬영
        t += T_HOLD; KEY.append((t, q))
    DONE_AT.append(t)                                 # 이 블레이드 검사 완료 시점
KEY.append((t + T_WAY, HOME))                          # 복귀 (루프)
DUR = t + T_WAY

def ease(x): return x*x*(3 - 2*x)

def pose_at(sec):
    if sec <= KEY[0][0]: return KEY[0][1]
    if sec >= KEY[-1][0]: return KEY[-1][1]
    for i in range(len(KEY) - 1):
        t0, q0 = KEY[i]; t1, q1 = KEY[i+1]
        if t0 <= sec <= t1:
            r = 0 if t1 == t0 else ease((sec - t0)/(t1 - t0))
            return [a + (b - a)*r for a, b in zip(q0, q1)]
    return KEY[-1][1]

def is_flash(sec):
    return any(s <= sec < s + 0.18 for s in SHOTS)

def apply_pose(sec):
    for i, j in enumerate(MOV):
        p.resetJointState(robot, j, pose_at(sec)[i])
    st = p.getLinkState(robot, EE, computeForwardKinematics=True)
    pos, orn = st[4], st[5]
    rot = p.getMatrixFromQuaternion(orn)
    fwd = [rot[0], rot[3], rot[6]]
    p.resetBasePositionAndOrientation(cam_body, [pos[i] + fwd[i]*0.030 for i in range(3)], orn)
    lens_orn = p.multiplyTransforms([0,0,0], orn, [0,0,0], p.getQuaternionFromEuler([0, math.pi/2, 0]))[1]
    p.resetBasePositionAndOrientation(lens_body, [pos[i] + fwd[i]*0.072 for i in range(3)], lens_orn)
    # 검사 끝난 블레이드는 색을 바꿔 '진행 상황'이 한눈에 보이게
    for i, dt in enumerate(DONE_AT):
        p.changeVisualShape(BLADES[i], -1,
                            rgbaColor=DONE_COLOR if sec >= dt else [0.62, 0.65, 0.70, 1])
    return pos, orn, fwd

# ── 카메라: 랙 전체가 보이도록 (반복이 눈에 들어와야 함) ──────
def view_at(sec):
    yaw = math.radians(-40 + 8*(sec/DUR))
    pitch = math.radians(-20)
    dist = 1.9
    tgt = [R_RACK*0.55, 0, Z_BLADE - 0.10]
    eye = [tgt[0] - dist*math.cos(pitch)*math.cos(yaw),
           tgt[1] - dist*math.cos(pitch)*math.sin(yaw),
           tgt[2] - dist*math.sin(pitch)]
    return p.computeViewMatrix(eye, tgt, [0, 0, 1])

PROJ = p.computeProjectionMatrixFOV(42, W/H, 0.05, 8.0)

# ── 실행 ────────────────────────────────────────────────────
if not RENDER:
    p.resetDebugVisualizerCamera(1.9, -40, -20, [R_RACK*0.55, 0, Z_BLADE - 0.10])
    print(f"\n  GUI 미리보기 - {DUR:.1f}초 루프 / 블레이드 {N_BLADE}장 x 3면 = {len(SHOTS)}컷")
    print("  창 닫으면 종료\n")
    t0 = time.time()
    while p.isConnected():
        apply_pose((time.time() - t0) % DUR)
        p.stepSimulation()
        time.sleep(1/FPS)
else:
    import numpy as np
    out = os.path.join(HERE, "frames")
    os.makedirs(out, exist_ok=True)
    for f in os.listdir(out):
        if f.endswith(".png"): os.remove(os.path.join(out, f))
    total = int(DUR*FPS)
    print(f"\n  렌더 - {total} 프레임 ({W}x{H} @ {FPS}fps) / {DUR:.1f}초 / {len(SHOTS)}컷")
    for f in range(total):
        sec = f/FPS
        apply_pose(sec)
        _, _, rgb, _, _ = p.getCameraImage(W, H, view_at(sec), PROJ,
                                           renderer=p.ER_BULLET_HARDWARE_OPENGL,
                                           lightDirection=[-0.6, -0.8, 1.2])
        img = np.reshape(np.array(rgb, dtype=np.uint8), (H, W, 4))[:, :, :3]
        if is_flash(sec):
            img = np.clip(img.astype(np.int16)*1.15, 0, 255).astype(np.uint8)
        _save_png(img, os.path.join(out, f"frame_{f:04d}.png"))
        if f % 60 == 0: print(f"    {f}/{total} ({sec:.0f}s)")
    print(f"\n  완료 -> {out}\n  다음:  py -3.11 make_mp4.py\n")
