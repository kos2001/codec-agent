# Phase 0 결과 리포트 — G.711 수동 파일럿

- 일자: 2026-09-17
- 브랜치: `feature/g711-phase0` (커밋 전)
- 계획: `spec-to-rtl-service-plan.md` §7 Phase 0, §10 hls-rtl 도입

## 1. 종료 조건 대비

| Phase 0 종료 조건 | 결과 | 근거 |
|---|---|---|
| L1 STL 공식 벡터 100% 일치 | ✅ | 6종 × 65536 샘플, RTL 출력 byte-identical |
| L2 전수 검사 100% 일치 | ✅ | 인코더 입력 65536 전부(sweep.src가 전수), 디코더 256 코드 전부 |
| L3 스펙 표 경계값 | ✅ | A-law 116 / μ-law 132 경계 벡터 |
| lint 경고 0 | ✅ | `verilator --lint-only -Wall` 4모듈 |
| 실패 유형 문서화 | ✅ | §3 |
| (추가) formal unbounded 증명 | ✅ | 8/8 task, k-induction 통과, cover 도달 |
| (추가) 뮤테이션 검출력 측정 | ✅ | §4 |

**게이트 판정: `PROVEN`** (`python3 pipeline/gate.py`, 약 5초)

```
  lint        pass  4 modules clean
  synthesize  pass  alaw_enc=93 alaw_dec=105 ulaw_enc=140 ulaw_dec=84 cells
  simulate    pass  16/16 tests pass
  formal      pass  8/8 tasks (prove: unbounded, cover: reached)
  equiv       pass  candidate is the baseline  (info)
VERDICT PROVEN
```

**이 판정이 증명하지 않는 것**: 스펙이 열어둔 동작(§3.6 결정값 경계 입력 — STL 관례 채택), 범위 제외 조항(§3.2 all-zero 억제, §3.5 변환표), 실제 라이브러리 기준 타이밍·면적·전력.

**스펙 원문 대조 (추가)**: `specs/g711/T-REC-G.711-198811-I-PDF-E.pdf` 확보 후 `tests/test_spec_tables.py`가 Table 1a/1b/2a/2b를 인쇄된 열·주석만으로 재구성해 디코더 256코드 전체와 인코더 구간 내부점 전체를 대조 — 전부 일치. 주입한 모델 버그 5종 모두 검출.

## 2. 산출물

| 파일 | 내용 |
|---|---|
| `generated/g711/requirements.yaml` | REQ-IF 5, REQ-A 8, REQ-U 8 |
| `generated/g711/rtl/*.sv` | 4모듈, valid/ready, 레지스터 1단, yosys generic 84–140 cells |
| `golden/g711/spec_model.py` | 스펙 공식 모델 (STL과 독립 작성) |
| `generated/g711/tb/` | iverilog TB, 테스트 16개 (L1 6, L2 2, L3 2, full-rate 6) |
| `generated/g711/formal/` | 불변식 A1–A6, cover C1–C3 |
| `pipeline/gate.py`, `pipeline/mutate.py` | 게이트, 뮤테이션 캠페인 |
| `tests/test_golden.py` | L0 22개 |

## 3. 실패 유형 — 자동화 루프가 잡아야 할 것

Phase 0의 핵심 산출물. 각 항목은 실제로 발생했고, 무엇이 잡았는지 기록한다.

| # | 결함 | 어디서 | 무엇이 잡았나 | 무엇이 **놓쳤나** |
|---|---|---|---|---|
| F1 | μ-law 전송 부호비트까지 반전 (+0 → 0x7F로 인코딩) | 스펙 해석 (spec_model) | STL 단방향 비교 (enc 0/65536, dec 2/256) | **왕복 테스트는 65536/65536 통과** — 인코더·디코더가 같은 방향으로 틀려 상쇄 |
| F2 | 시프트량 `3'd2 + seg` 3비트 오버플로 (seg 6/7) | RTL 작성 | 코드 리뷰 (이후 뮤턴트 M1로 재현 시 L1 dec/L2 검출) | — |
| F3 | ITU PDF 스크립트 다운로드 차단 (curl: "Request Rejected"/500) | 스펙 수집 | curl 결과 HTML 확인 → 헤드리스 브라우저 세션 내 fetch로 확보 | 파일 확장자만 보면 성공처럼 보임 |
| F13 | 스펙 PDF의 Table 1/2가 텍스트 레이어 없는 그림 — 텍스트 추출이 표를 통째로 누락 | 스펙 추출 (서비스 ①단계) | 페이지 렌더링 후 시각 판독 | **텍스트 추출기** — 오류 없이 표 없는 텍스트를 반환 |
| F4 | STL CTest가 in-source 작업 디렉터리 가정 → 전부 FAIL | 골든 빌드 | 로그 확인 | "테스트 실패"로 오판할 뻔함 (실제로는 미실행) |
| F5 | CMake 3.5 미만 호환 제거로 STL 구성 실패 | 골든 빌드 | 에러 메시지 | — |
| F6 | TB `$urandom(seed)` 사용법 오류로 전 테스트 미실행 | TB | 결과 줄 부재 | — |
| F7 | 뮤테이션 하니스가 `head -4`로 RESULT 줄을 잘라 "검출 0건" 보고 | 검증 인프라 | 뮤턴트 1개 수동 실행 | **하니스 자체** — "전부 통과"를 설계 품질로 오해할 뻔함 |
| F8 | 처리량 저하 뮤턴트(`in_ready = out_ready`)를 TB가 못 잡음 | 검증 공백 | hls-rtl full-rate guard 도입 후 | 랜덤 backpressure TB (값·순서만 비교) |
| F9 | `out_valid` 리셋 누락을 시뮬레이션이 못 잡음 (posedge 리셋 해제 race + 우호적 리셋 환경) | 검증 공백 | formal `A6_reset_empties` → TB 강화 후 시뮬레이션도 검출 | 기존 TB 리셋 검사 |
| F10 | `equiv_make/equiv_induct`가 등가 뮤턴트(`1FFF→1FFE`)를 증명 못함 | 검증 인프라 | 수동 miter 실험 | hls-rtl 방식 LEC |
| F11 | 데이터 레지스터 리셋값 뮤턴트 6개 생존 (관측 불가 로직) | RTL 설계 | 뮤테이션 캠페인 | — → 데이터 경로 리셋 제거 |
| F12 | L0 테스트의 "μ-law 음수 0(0x7F)은 인코더가 만들지 않는다" 가정이 틀림 | 테스트 작성자(나) | pytest 실패 + STL 일치 확인 | — |

**자동화 설계에 주는 시사점**
1. 왕복 테스트는 코덱 검증 근거가 될 수 없다 (F1) → L1은 enc/dec 단방향 벡터를 분리 유지.
2. "통과"는 검증 인프라가 실패를 **볼 수 있음**을 보인 뒤에만 의미가 있다 (F4, F6, F7) → 게이트는 결과 줄 부재를 FAIL로, 뮤테이션 캠페인을 정기 실행.
3. 시뮬레이션과 formal은 서로의 공백을 메운다 (F9: formal만 검출 → TB 강화).
4. LLM이 가장 틀리기 쉬운 곳은 비트 수준 관례(부호비트 반전, 1의 보수 경계, 시프트 폭)다 (F1, F2) → `failure_evidence()`는 첫 불일치 샘플의 stim/got/exp를 그대로 넘긴다.

## 4. 뮤테이션 커버리지

캠페인: `runs/20260917-222405-mutate.json` (165개, 170초, 8병렬) + dropped-update 재실행 `runs/20260917-222446-mutate.json` (12개). 모든 뮤턴트에 **모든 gating stage**를 실행.

| 항목 | 값 |
|---|---|
| 뮤턴트 | 177 (4모듈) |
| 등가 뮤턴트 (equiv가 증명, 분모에서 제외) | 1 — μ-law 포화값 `13'h1FFF→1FFE`: 포화 후 하위 비트는 출력에 쓰이지 않음 |
| **검출** | **176 / 176 (100%)**, 생존 0 |

| 연산자 | 검출 | 생존 | 등가 |
|---|---|---|---|
| arithmetic | 5 | 0 | 0 |
| boolean | 7 | 0 | 0 |
| constant | 74 | 0 | 1 |
| dropped-update | 12 | 0 | 0 |
| inversion | 16 | 0 | 0 |
| mux-swap | 5 | 0 | 0 |
| relational | 1 | 0 | 0 |
| slice-shift | 48 | 0 | 0 |
| stuck-at | 8 | 0 | 0 |

stage별 검출 수 (중복 포함): lint 44, synthesize 2, simulate 176, formal 176. **단독 검출은 0** — simulate와 formal이 각각 전체를 잡는다. 서로 독립적인 두 증거가 같은 결론을 낸다는 뜻이고, 한쪽이 skip되어도 검출력이 유지된다.

**100%에 이르기까지의 이력 (숫자만 보면 안 되는 이유)**

| 회차 | 결과 | 원인 / 조치 |
|---|---|---|
| 수동 1차 | 7종 모두 "검출 0" | 하니스 버그 (F7) |
| 자동 1차 | 176/183 (96.2%), formal 단독 2 | 리셋 누락 2건을 시뮬레이션이 못 봄 (F9) → TB 적대적 리셋; 데이터 리셋값 뮤턴트 6건 관측 불가 (F11) → 데이터 리셋 제거; 등가 뮤턴트 오분류 (F10) → miter LEC |
| 자동 2차 | 164/164 (100%) | **단, dropped-update 연산자가 0개 생성됨** — RTL 형식 변경으로 정규식 불일치. 100%가 부풀려짐 |
| 보완 | +12/12 | 정규식 수정, 연산자별 생성 0개면 캠페인 실패하도록 가드 추가 |

**한계**: 단일 결함만 주입. 뮤턴트는 연산자 목록이 표현하는 결함 유형에 한정 (예: 여러 줄에 걸친 구조 변경, 타이밍 결함은 대상 아님).

## 5. hls-rtl 도입 요약

상세는 계획서 §10. 도입: 단일 게이트·skip 규칙·PROVES 문구·failure_evidence·SHA 증거·throughput guard·리셋 테스트·black-box formal·mutation·LEC·L0·CLAUDE.md 규칙. 미도입: SystemC, 웹/Hermes/KB, cocotb, style linter, line coverage.

hls-rtl 대비 달라진 점:
- datapath formal이 **unbounded, full-width** (레지스터 1단이라 shadow 관계가 inductive). hls-rtl은 메모리 때문에 축소 bounded.
- 게이트가 5초라 뮤턴트마다 **모든 stage를 실행** → "첫 검출 stage"가 아닌 검출 매트릭스, formal 단독 검출 수를 측정.
- LEC를 `miter + sat -tempinduct`로 교체 (F10).

## 6. 다음 (Phase 1)

1. `pipeline/`에 ①② (스펙 추출 → requirements.yaml) + 사람 승인 게이트
2. ④ LLM RTL 생성 → `gate.prove()` → 반려 시 `failure_evidence()` 피드백, 결과는 `PROVEN_CHANGE / ORACLE_CONFLICT / NO_CHANGE / EXHAUSTED`
3. 재현성: 동일 requirements로 N회 생성해 PROVEN 도달률·반복 횟수 측정
4. ~~G.711 PDF 확보 후 requirements.yaml 원문 대조~~ ✅ 완료 — ①단계 설계에 반영: 표가 그림인 PDF가 있으므로 텍스트 추출 + 페이지 렌더링(비전) 병행, 추출 표는 인쇄된 합계·끝점으로 자체 검증

## 7. Phase 1 착수 — Hermes 생성 (2026-09-17~18)

Hermes Agent API 서버(profile `codecagent`, :8659, OpenRouter `deepseek/deepseek-v4.1-flash`, tools off).
프롬프트에는 requirements + port contract만 — 커밋 RTL/기대값 없음 (`tests/test_generate.py`가 강제).

**RTL 생성** (`pipeline/generate.py`, gate가 판정)

| module | 결과 | 반복 | 비고 |
|---|---|---|---|
| alaw_enc | PROVEN | 1 | 유효 출력 기준 ≡ baseline |
| alaw_dec | PROVEN | 2 | iter1 lint(WIDTHEXPAND) 반려 → 근거로 수리 |
| ulaw_enc | PROVEN | 2 | 첫 run은 비동기 리셋으로 PROVEN → **게이트 빈틈**. 합성 단계 REQ-IF-04 검사 추가 후 재생성: iter1 반려 → iter2 PROVEN |
| ulaw_dec | PROVEN | 2 | 동일 |

**Testbench 생성** (`pipeline/generate_tb.py`: soundness = 커밋 RTL에서 PASS, sensitivity = 뮤턴트 kill)

| module | 결과 | 체크 수 | kill | 생존 뮤턴트 |
|---|---|---|---|---|
| ulaw_enc | ACCEPTED (3회) | 147,635 | 64/64 | (reset-style 연산자 추가 전 측정) |
| alaw_dec | ACCEPTED (1회) | 315 | 26/28 | async reset, `in_ready` stuck-0 (TB가 수신 개수를 검사 안 함) |
| ulaw_dec | ACCEPTED (2회) | 765 | 23/24 | async reset |
| alaw_enc | 1차 EXHAUSTED → 재시도 ACCEPTED (1회) | 131,292 | 60/61 | async reset |

**2차 (보강 후, §8)** — 계약에 클럭 에지 사이 리셋 검사·수신 개수 검사 추가, 결함 유형별 최소 검출 요구

| module | 결과 | 체크 수 | kill |
|---|---|---|---|
| alaw_enc | ACCEPTED (1회) | 238,432 | 61/61 |
| alaw_dec | ACCEPTED (2회) | 368 | 28/28 |
| ulaw_enc | ACCEPTED (3회) | 196,929 | 65/65 |
| ulaw_dec | ACCEPTED (2회) | 776 | 24/24 |

**추가된 실패 유형**

| # | 결함 | 발견 | 조치 |
|---|---|---|---|
| F14 | 게이트가 비동기 리셋 RTL을 PROVEN (sim/formal 모두 클럭 에지 샘플링) | 생성 RTL의 equiv 불일치 조사 | `synthesize`에 `$adff` 금지, `reset-style` 뮤턴트 → 181개 중 180/180 검출, 4개는 synthesize 단독 |
| F15 | equiv가 도구 오류(async FF 모델 불가)를 "differs"로 보고 | 반례 추출 시도 | `proof did fail`만 differs, 그 외 skip |
| F16 | equiv가 don't-care 데이터(out_valid=0) 차이로 불일치 | 생성 RTL 검토 | out_valid로 마스킹한 valid-qualified miter |
| F17 | 생성 프롬프트에 lint 계약 부재 → sim/formal 통과해도 반려 | 첫 deepseek run | 프롬프트에 `-Wall` 계약 명시 |
| F18 | 파형 창을 10 ns 주기로 가정 → timescale 없는 생성 TB에서 빈 파형 | 파형 확인 | clock edge 수로 창 지정 |
| F19 | TB의 입력/출력 짝이 어긋난 것을 "기대값 오류"로 안내 | alaw_enc TB EXHAUSTED 분석 | 증명된 RTL과 모순되는 불일치를 misalignment로 안내 |

## 8. 실패 사례 보강 (2026-09-18)

1차 실행에서 fail로 끝난 사례를 분류하고, 각각을 도구가 잡도록 보강했다.

| 실패 사례 | 건수 | 보강 | 확인 |
|---|---|---|---|
| 비동기 리셋 RTL이 게이트를 통과 / 생성 TB가 놓침 | RTL 4, TB 3 | `tb_g711`에 **클럭 에지 사이 리셋 검사** 추가(출력 유지 상태에서 rst_n을 내려도 다음 posedge까지 불변), TB 계약에도 동일 항목 | 뮤테이션 재측정: `reset-style` 4건을 **synthesize + simulate 양쪽**이 검출, **단독 검출 stage 0** |
| TB가 약해서 결함을 놓침 (`in_ready` stuck-0 등) | 4 | 유형별 최소 검출 요구(`REQUIRED_CLASSES`), WEAK도 수리 루프 진입, 힌트는 **동작 수준**(RTL 비노출) | 재생성 4/4 ACCEPTED, 전부 100% kill, blind class 0 |
| TB 불건전 (실행 오류·참조 모델 오류·입출력 짝 어긋남) | 6 | 프롬프트에 iverilog 제약(`$urandom(seed)`), 근거에 misalignment 진단 추가 | 2차 생성에서 불건전 반복 3회→최대 2회 |
| RTL 반려 (lint 폭, 비동기 리셋) | 5 | 프롬프트에 lint 계약 + **동기 리셋 명시** | — (재생성 시 확인 예정) |
| 클라이언트 타임아웃으로 ERRORED | 3 | `HERMES_TIMEOUT` 상향(600s → 2400s, 실측 13분) | 재실행 3/3 정상 완료 |

**뮤테이션 재측정 (`runs/20260918-110525-mutate.json`)**: 181개 중 등가 1개 제외 **180/180 검출**,
stage별 검출 lint 44 · synthesize 6 · simulate 180 · formal 176, **단독 검출 0** —
어느 한 stage가 빠져도 검출력이 유지된다.

## 9. 지식베이스(KB)와 자기개선 루프 (2026-09-18)

`lsi_error_analyzer`(error-analysis)의 설계를 옮겨왔다: **해결된 과거 사례가 자산**,
검색은 BM25 + 엔티티 중첩 RRF + coverage 게이트, 저장소는 **커밋 추적 JSON**(버전·리뷰·공유),
그리고 L1(측정·진단, 부작용 없음) / L2(실행 + 회귀 게이트) 분리.

### KB (`pipeline/kb.py`, `data/kb.json`)

사례는 `runs/`에서 자동으로 캔다 — **반려된 시도 + 게이트가 낸 근거 + 다음 시도에서 통과시킨 diff**.
여기에 `CLAUDE.md`의 규칙을 사례로 추가한다. 현재 30건(규칙 22 / RTL 3 / TB 5).

### KB가 도움이 되는가 — A/B 실험 (`pipeline/kb_experiment.py`, `runs/*-kb-experiment.json`)

과거 반려 사례를 고정 과제로 두고 **같은 수리 요청을 KB 있음/없음으로** 보낸 뒤 도구가 채점한다.
누수 방지: 그 사례 자신의 실행 기록은 KB에서 제외 — 검색 결과는 **다른 실패**에서만 올 수 있다.

| 과제 | KB 없음 | KB 있음 |
|---|---|---|
| RTL 수리 3건 (lint 1, 비동기 리셋 2) | 3/3 | 3/3 |
| TB 수리 6건 (모두 불건전 사례) | 3/6 | 3/6 |
| **합계** | **6/9** | **6/9** |

**결론: 현재 규모에서 KB의 이득은 측정되지 않았다.** RTL 수리는 게이트 근거만으로 충분해 천장 효과가
났고, TB 수리는 KB 유무와 무관하게 절반이 실패했다. 검색 자체도 leave-one-out P@1 0.286 (답이
가능한 7건 기준) — 같은 유형 사례가 2건 이상 쌓인 라벨이 `synthesize`뿐이다.
과장하지 않고 그대로 기록하고, 루프가 이 두 가지를 계속 지적하게 두었다(`kb.no_benefit`,
`kb.retrieval_weak`). 다음에 확인할 가설: (a) 사례가 수십 건 쌓인 뒤 재측정, (b) 수리 단계가 아니라
**첫 생성 프롬프트**에 규칙 사례를 주입.

### 자기개선 루프 (`pipeline/improve.py`, 워크벤치 Improve 탭)

- **L1 측정**: 게이트 판정·단계, L0, 뮤테이션 검출 매트릭스(생존/단독 검출), 생성물 품질(RTL outcome·
  재판정, TB kill·blind class), KB 사례 수·검색 평가·A/B 요약.
- **L1 진단**: 빠진 증거를 우선순위로 나열. 예) 건너뛴 게이트 단계(P1), 생존 뮤턴트(P1),
  단독 검출 단계(P2), blind class를 가진 TB(P2), KB 낙후(P3).
- **L2 실행 + 회귀 게이트**: 지적마다 실행 명령이 붙고, 실행 후 재측정해 **주요 지표가 하나라도
  나빠지면 회귀로 보고하고 루프를 멈춘다.** 이력은 `data/improve_history.json`에 적재.
- 판정은 전부 도구(게이트·pytest·뮤테이션·KB 평가)가 한다. 테스트 15건으로 진단 규칙과 회귀 게이트를 고정.

현재 스냅숏: gate PROVEN · L0 66 통과 · mutation 100%(단독 검출 0) · TB 최저 kill 100% ·
RTL 4/4 PROVEN · 열린 지적은 KB 2건뿐.
