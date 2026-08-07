"""
D-008 PyBullet 시뮬레이션 - HPC 블레이드 검사 촬영
  시나리오 문서 D008_시뮬레이션_시나리오_v1.md 규격 구현

  증명하는 것:
    "로봇이 블레이드 한 개를 같은 조건(각도.거리.조명)으로 3면 촬영하고,
     품질이 미달하면 파라미터를 보정해 다시 찍는다."

  실행:
    py -3.11 sim_blade.py            # GUI 미리보기 (빠른 확인용)
    py -3.11 sim_blade.py --render   # 오프스크린 렌더 -> frames/*.png -> mp4
    py -3.11 sim_blade.py --render --pov   # + POV 스틸 3장 (슬라이드용)

  ※ 영상에 자막.라벨.bbox 없음 (데모 HTML이 오버레이 담당 - 시나리오 §3)
"""
import pybullet as p
import pybullet_data
import math, os, sys, time, zlib, struct

def _save_png(rgb, path):
    """numpy RGB 배열 -> PNG (외부 라이브러리 없이 표준 모듈만)"""
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
RENDER = "--render" in ARGS          # 오프스크린 렌더 모드
POV    = "--pov" in ARGS             # POV 스틸 추가 산출
HERE   = os.path.dirname(os.path.abspath(__file__))
BLADE_OBJ = os.path.join(HERE, "blade", "hpt_blade.obj").replace("\\", "/")

FPS = 30
DUR = 23                             # 총 23초 루프
W, H = (1920, 1080) if RENDER else (1280, 720)

# ── 연결 ────────────────────────────────────────────────────
p.connect(p.DIRECT if RENDER else p.GUI, options=f"--width={W} --height={H}")
p.configureDebugVisualizer(p.COV_ENABLE_SHADOWS, 1)
p.setAdditionalSearchPath(pybullet_data.getDataPath())

# ── 씬 (시나리오 §1) ────────────────────────────────────────
def box(pos, half, rgba, orn=None):
    v = p.createVisualShape(p.GEOM_BOX, halfExtents=half, rgbaColor=rgba)
    c = p.createCollisionShape(p.GEOM_BOX, halfExtents=half)
    return p.createMultiBody(0, c, v, basePosition=pos,
                             baseOrientation=orn or [0, 0, 0, 1])

def cyl(pos, r, h, rgba, orn=None):
    v = p.createVisualShape(p.GEOM_CYLINDER, radius=r, length=h, rgbaColor=rgba)
    c = p.createCollisionShape(p.GEOM_CYLINDER, radius=r, height=h)
    return p.createMultiBody(0, c, v, basePosition=pos,
                             baseOrientation=orn or [0, 0, 0, 1])

# 바닥 - 어두운 회색 (데모 UI 톤과 맞춤)
plane = p.loadURDF("plane.urdf")
p.changeVisualShape(plane, -1, rgbaColor=[0.16, 0.18, 0.21, 1], textureUniqueId=-1)

STAND_H, STAND_X = 0.75, 0.62
# 검사 스탠드: 얇은 상판 + 가는 다리 4개 (통짜 박스면 로봇 팔이 파고들어 겹쳐 보임)
TOP_HX, TOP_HY, TOP_HZ = 0.12, 0.12, 0.010          # 상판 24x24cm, 두께 2cm (팔 간섭 최소화)
LEG_H = STAND_H - TOP_HZ
box([STAND_X, 0, STAND_H], [TOP_HX, TOP_HY, TOP_HZ], [0.28, 0.32, 0.38, 1])    # 상판
for lx in (-1, 1):
    for ly in (-1, 1):
        box([STAND_X + lx*(TOP_HX - 0.03), ly*(TOP_HY - 0.03), LEG_H/2],
            [0.016, 0.016, LEG_H/2], [0.19, 0.22, 0.27, 1])                     # 다리
box([STAND_X, 0, STAND_H + TOP_HZ + 0.115], [0.035, 0.035, 0.115], [0.30, 0.33, 0.38, 1])  # 거치 포스트
box([STAND_X, 0, STAND_H + TOP_HZ + 0.205], [0.055, 0.055, 0.03], [0.33, 0.36, 0.41, 1])   # 고정구(바이스)

# HPC 블레이드 - Sketchfab HPT Blade (CC BY, nikdanialaiman109)
BLADE_BOTTOM = STAND_H + TOP_HZ + 0.22   # 팔이 상판을 스치지 않도록 거치대 높임
bv = p.createVisualShape(p.GEOM_MESH, fileName=BLADE_OBJ, meshScale=[0.0159]*3,
                         rgbaColor=[0.62, 0.65, 0.70, 1],
                         specularColor=[0.45, 0.45, 0.5])
blade = p.createMultiBody(0, -1, bv, basePosition=[STAND_X, -0.04, BLADE_BOTTOM])
BLADE_MID = BLADE_BOTTOM + 0.11          # 촬영 타깃 높이

# 로봇 - 페데스탈 위 Indy7
PED_H = 0.62
cyl([0, 0, PED_H/2], 0.13, PED_H, [0.20, 0.22, 0.26, 1])
robot = p.loadURDF("indy7.urdf", basePosition=[0, 0, PED_H], useFixedBase=True)
EE = 7
MOV = [j for j in range(p.getNumJoints(robot)) if p.getJointInfo(robot, j)[2] == p.JOINT_REVOLUTE]

# 카메라 헤드 (IndyEye 상징) - 플랜지에 부착된 것처럼 매 프레임 따라다님
cam_body = box([0, 0, -5], [0.035, 0.045, 0.028], [0.13, 0.15, 0.18, 1])
lens_body = cyl([0, 0, -5], 0.018, 0.03, [0.08, 0.09, 0.11, 1])

# ── 포즈 (IK로 1회 계산 후 키프레임으로 사용 - 시나리오 §2) ──
D = 0.14                                  # 작동거리 14cm (IndyEye 5~70cm 범위 내)
BLADE_CTR = [STAND_X, -0.02, BLADE_MID]   # 블레이드 중심 (카메라가 향할 지점)

def look_orn(eye):
    """eye 위치에서 블레이드 중심을 바라보는 엔드이펙터 방향.
       링크 로컬 +X가 카메라 광축이므로 +X가 블레이드를 향하게 pitch/yaw 계산."""
    dx, dy, dz = (BLADE_CTR[i] - eye[i] for i in range(3))
    yaw = math.atan2(dy, dx)
    pitch = -math.atan2(dz, math.hypot(dx, dy))
    return p.getQuaternionFromEuler([0, pitch, yaw])

CY = -0.02                                           # 블레이드 y 중심
HOME      = [0, math.radians(-100), math.radians(115), 0, math.radians(-10), 0]   # 블레이드 앞 대기

# 관절 한계·시작자세(rest pose)를 주어 '위에서 내려다보는' 자연스러운 해를 유도
_LO = [-3.0]*len(MOV); _HI = [3.0]*len(MOV); _RNG = [6.0]*len(MOV)
def ik(target):
    """목표 위치 + '블레이드를 바라보는' 자세로 IK (rest pose 기준 자연스러운 해)"""
    for i, j in enumerate(MOV):                      # 매번 HOME에서 출발시켜 해를 안정화
        p.resetJointState(robot, j, HOME[i])
    q = p.calculateInverseKinematics(robot, EE, target, look_orn(target),
                                     lowerLimits=_LO, upperLimits=_HI,
                                     jointRanges=_RNG, restPoses=HOME,
                                     maxNumIterations=600, residualThreshold=1e-5)
    return list(q[:len(MOV)])
LEADING   = ik([STAND_X - D, CY, BLADE_MID])         # 리딩엣지 (전연) - 로봇쪽 정면
PRESSURE  = ik([STAND_X, CY - D, BLADE_MID])         # 압력면 - 측면 1
SUCTION   = ik([STAND_X, CY + D, BLADE_MID])         # 흡입면 - 측면 2
# 판단② 보정 포즈: 거리 -5cm, 각도 +8° (시나리오 §2 H구간)
_a = math.radians(8); _d2 = D - 0.01
RETAKE    = ik([STAND_X + _d2*math.sin(_a), CY + _d2*math.cos(_a), BLADE_MID])
BACKOFF   = ik([STAND_X, CY + D + 0.10, BLADE_MID])  # 살짝 물러남

# 키프레임: 초 -> 관절각 (시나리오 §2 타임라인)
KEY = [
    (0.0,  HOME),      (2.0,  HOME),       # A 대기
    (5.0,  LEADING),   (7.0,  LEADING),    # B 접근 / C 촬영1
    (10.0, PRESSURE),  (12.0, PRESSURE),   # D 이동 / E 촬영2
    (15.0, SUCTION),   (17.0, SUCTION),    # F 이동 / G 촬영3
    (18.2, BACKOFF),                       # H 판단② - 물러남
    (20.0, RETAKE),    (21.0, RETAKE),     #   보정 재접근 + 재촬영
    (23.0, HOME),                          # I 복귀 (A와 동일 = 루프)
]
# 촬영 구간(정지 = 촬영). 각 구간 시작 0.2초간 플래시
SHOTS = [(5.0, 7.0), (10.0, 12.0), (15.0, 17.0), (20.0, 21.0)]

def ease(t):                                # ease-in-out
    return t*t*(3 - 2*t)

def pose_at(sec):
    if sec <= KEY[0][0]: return KEY[0][1]
    if sec >= KEY[-1][0]: return KEY[-1][1]
    for i in range(len(KEY) - 1):
        t0, q0 = KEY[i]; t1, q1 = KEY[i+1]
        if t0 <= sec <= t1:
            r = 0 if t1 == t0 else ease((sec - t0) / (t1 - t0))
            return [a + (b - a)*r for a, b in zip(q0, q1)]
    return KEY[-1][1]

def is_flash(sec):                           # 촬영 순간 0.2초
    return any(s <= sec < s + 0.2 for s, _ in SHOTS)

def apply_pose(sec):
    for i, j in enumerate(MOV):
        p.resetJointState(robot, j, pose_at(sec)[i])
    # 카메라 헤드를 플랜지 위치로
    st = p.getLinkState(robot, EE, computeForwardKinematics=True)
    pos, orn = st[4], st[5]
    rot = p.getMatrixFromQuaternion(orn)
    fwd = [rot[0], rot[3], rot[6]]
    # 본체는 광축(+X) 뒤쪽에 -> 렌즈가 대상 쪽을 향하는 모양
    p.resetBasePositionAndOrientation(
        cam_body, [pos[i] + fwd[i]*0.030 for i in range(3)], orn)
    # 렌즈: 원기둥 길이축(+Z)을 광축(+X)에 맞춘 뒤 EE 회전 적용
    lens_orn = p.multiplyTransforms([0, 0, 0], orn,
                                    [0, 0, 0], p.getQuaternionFromEuler([0, math.pi/2, 0]))[1]
    p.resetBasePositionAndOrientation(
        lens_body, [pos[i] + fwd[i]*0.072 for i in range(3)], lens_orn)
    return pos, orn, fwd

# ── 메인 카메라 (시나리오 §3): 블레이드 중심, 1.5m, pitch -15°, yaw 10° 서행 ──
def view_at(sec):
    yaw = math.radians(-38 + 10*(sec/DUR))
    pitch = math.radians(-15)
    dist = 1.35
    tgt = [STAND_X - 0.14, -0.02, BLADE_MID]
    eye = [tgt[0] - dist*math.cos(pitch)*math.cos(yaw),
           tgt[1] - dist*math.cos(pitch)*math.sin(yaw),
           tgt[2] - dist*math.sin(pitch)]
    return p.computeViewMatrix(eye, tgt, [0, 0, 1])

PROJ = p.computeProjectionMatrixFOV(38, W/H, 0.05, 8.0)

# ── 실행 ────────────────────────────────────────────────────
if not RENDER:
    # GUI 미리보기: 실시간 루프 재생
    p.resetDebugVisualizerCamera(1.6, 48, -15, [STAND_X - 0.06, 0, BLADE_MID])
    print("\n  GUI 미리보기 - 23초 루프 재생 (창 닫으면 종료)")
    print("  최종 영상은:  py -3.11 sim_blade.py --render\n")
    t0 = time.time()
    while p.isConnected():
        sec = (time.time() - t0) % DUR
        apply_pose(sec)
        p.stepSimulation()
        time.sleep(1/FPS)
else:
    import numpy as np
    out = os.path.join(HERE, "frames")
    os.makedirs(out, exist_ok=True)
    for f in os.listdir(out):
        if f.endswith(".png"):
            os.remove(os.path.join(out, f))

    total = DUR * FPS
    pov_saved = []
    print(f"\n  렌더 시작 - {total} 프레임 ({W}x{H} @ {FPS}fps)")
    for f in range(total):
        sec = f / FPS
        pos, orn, fwd = apply_pose(sec)
        _, _, rgb, _, _ = p.getCameraImage(
            W, H, view_at(sec), PROJ,
            renderer=p.ER_BULLET_HARDWARE_OPENGL,
            lightDirection=[-0.6, -0.8, 1.2])
        img = np.reshape(np.array(rgb, dtype=np.uint8), (H, W, 4))[:, :, :3]
        if is_flash(sec):                       # 촬영 플래시 +15%
            img = np.clip(img.astype(np.int16) * 1.15, 0, 255).astype(np.uint8)
        # PNG 저장 (외부 라이브러리 없이)
        _save_png(img, os.path.join(out, f"frame_{f:04d}.png"))

        # POV 스틸 (각 촬영 정지 중앙 시점 1장씩)
        if POV:
            for i, (s, e) in enumerate(SHOTS[:3]):
                mid = (s + e) / 2
                if abs(sec - mid) < 1/FPS/2 and i not in pov_saved:
                    vm = p.computeViewMatrix(pos, [pos[k] + fwd[k]*0.3 for k in range(3)], [0, 0, 1])
                    _, _, prgb, _, _ = p.getCameraImage(
                        1280, 1280, vm, p.computeProjectionMatrixFOV(50, 1.0, 0.01, 3.0),
                        renderer=p.ER_BULLET_HARDWARE_OPENGL)
                    pimg = np.reshape(np.array(prgb, dtype=np.uint8), (1280, 1280, 4))[:, :, :3]
                    _save_png(pimg, os.path.join(HERE, f"pov_{['leading','pressure','suction'][i]}.png"))
                    pov_saved.append(i)

        if f % 30 == 0:
            print(f"    {f}/{total}  ({sec:.0f}s)")
    print(f"\n  완료 - {out}")
    print( "  mp4 인코딩:")
    print(f'    ffmpeg -framerate {FPS} -i "{out}/frame_%04d.png" -c:v libx264 -pix_fmt yuv420p -crf 23 "{HERE}/sim.mp4"\n')
