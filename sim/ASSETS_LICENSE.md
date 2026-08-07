# 시뮬레이션 자산 출처·라이선스

> D-008 PyBullet 블레이드 검사 시뮬레이션에 사용한 3D 자산의 출처.
> 코치 요구사항(출처 슬라이드)에 그대로 사용 가능.

## 1. HPC 블레이드 모델

| 항목 | 내용 |
| --- | --- |
| 모델명 | **HPT Blade** |
| 작성자 | **nikdanialaiman109** |
| 출처 | Sketchfab |
| URL | https://sketchfab.com/3d-models/hpt-blade-a362b9662f6a4e8587837fbb897ca0c6 |
| 라이선스 | **CC Attribution (CC BY)** — 출처 표기 시 사용·수정 자유 |
| 파일 | `HPT plain.obj` (3.9MB · 정점 16,906 · 면 33,789) |
| 적용 | meshScale `0.0159` → 실제 높이 **21.9cm** (시나리오 스펙 20~25cm 충족) |

> **표기 의무**: CC BY는 출처 표시가 필수입니다. 발표 자료에 위 작성자·URL·라이선스를 명시하세요.
> 예: `HPT Blade by nikdanialaiman109 (Sketchfab, CC BY)`

## 2. Indy7 로봇 모델

| 항목 | 내용 |
| --- | --- |
| 모델 | **Indy7 URDF** (`indy7.urdf` + meshes) |
| 출처 | 뉴로메카 공식 ROS 패키지 — https://github.com/neuromeka-robotics/indy-ros2 |
| 패키지 | `indy_description` (브랜치: jazzy-indyDCP3) |
| 비고 | 협동로봇 실제 사양 모델 — 관절 6축(REVOLUTE) + TCP |

## 3. 그 외

- 스탠드·고정구·페데스탈 등 씬 구성물: **PyBullet 프리미티브(박스·원기둥)** 로 직접 생성 — 라이선스 이슈 없음
