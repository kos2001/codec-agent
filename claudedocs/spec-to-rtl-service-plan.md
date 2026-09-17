# Spec → RTL 생성 서비스 계획 (G.711 파일럿)

- 작성일: 2026-09-17
- 상태: Phase 0 완료 (결과: `phase0-report.md`), Phase 1 착수 전
- 참고 레포: [openitu/STL](https://github.com/openitu/STL) (골든 모델/벡터), hoangt/ASIC-Design · kelu124/lit3rick (RTL 구조 참고, 라이선스 미명시 → 코드 복사 금지), **`../hls-rtl`** (검증 게이트·formal·mutation 방법론, §10)

---

## 1. 목표와 범위

**목표**: 표준 스펙 문서(PDF)를 입력받아, **표준 참조 구현과 비트 단위로 일치하는** 합성 가능한 RTL과 검증 리포트를 자동 생성하는 서비스.

**핵심 원칙**: "LLM이 RTL을 잘 쓰는가"가 아니라 **"골든 모델과 불일치하면 통과시키지 않는 닫힌 루프"**가 서비스의 본체다.

| 구분 | 포함 | 제외 (Phase 1) |
|---|---|---|
| 스펙 | ITU-T G.711 본문 (A-law, μ-law, Table 1/2) | Appendix I (PLC), Appendix II (CNG) |
| 산출물 | SV RTL, 테스트벤치, 요구사항 추적표, lint/합성 리포트 | 타이밍 클로저, 물리 설계, FPGA 보드 bring-up |
| 확장 대상 | G.726 ADPCM → 이후 다른 스펙 | 비디오 코덱 (규모가 달라 별도 계획) |

---

## 2. STL 레포에서 확인한 사실 (계획의 근거)

`openitu/STL/src/g711/` 조사 결과:

- **API**: `alaw_compress / alaw_expand / ulaw_compress / ulaw_expand (long lseg, short *in, short *out)` — 16비트 short 버퍼 기반
- **테스트 벡터** (`test_data/`, 각 131072 bytes = 16비트 65536 샘플):
  - `sweep.src` — 입력 (16비트 전 범위 sweep)
  - `sweep-r.a`, `sweep-r.u` — lin → log 기대값 (A/μ)
  - `sweep-r.rea`, `sweep-r.reu` — log → lin 기대값
  - `sweep-r.a-a`, `sweep-r.u-u` — lin → log → lin 왕복 기대값
- **CTest**가 `g711demo` 실행 후 `cf`로 기대값과 비교하는 구조 → 같은 방식으로 RTL 출력 비교 가능
- 라이선스: ITU-T General Public License (골든 모델로 **실행**하는 용도로 사용, RTL에 코드 이식하지 않음)

**설계 결정**: RTL 입출력을 STL과 동일한 **16비트 left-justified PCM**으로 맞춘다 → 공식 벡터 6종을 그대로 재사용.

---

## 3. 서비스 아키텍처

```
 [Spec PDF] ─► ① Spec Ingest ─► ② Spec Analyst ─► requirements.yaml ─► (사람 리뷰 게이트)
                                                         │
                     ┌───────────────────────────────────┼───────────────────────┐
                     ▼                                   ▼                       ▼
            ③ Golden Model Builder              ④ RTL Generator        ⑤ Testbench Generator
            - STL g711.c 빌드                    - micro-arch 문서       - exhaustive sweep
            - 스펙 표 기반 Python 모델            - SV RTL               - STL 벡터 재생
            - 둘을 교차 비교 (해석 오류 검출)                              - 스펙 표 경계값
                     │                                   │                       │
                     └──────────────► ⑥ Verify Loop ◄────┴───────────────────────┘
                                      iverilog/verilator 실행 → diff
                                      불일치 → 실패 샘플+REQ-ID를 ④에 피드백 (최대 N회)
                                                         │ 통과
                                                         ▼
                                      ⑦ Quality Gate: verilator lint, yosys synth/stat
                                                         ▼
                                      ⑧ Report: 추적표(REQ→RTL→Test), 커버리지, 면적
```

### 단계별 책임

| # | 단계 | 입력 → 출력 | 구현 방식 | 실패 시 |
|---|---|---|---|---|
| ① | Spec Ingest | PDF → 텍스트 + 표(CSV) | pdf 파서 + LLM 표 정규화 | 표 셀 수/행 수 검증 실패 → 사람 확인 |
| ② | Spec Analyst | 텍스트/표 → `requirements.yaml` (REQ-ID, 인터페이스, 수치표) | LLM | **사람 승인 없이는 다음 단계 진행 불가** |
| ③ | Golden Model | STL 소스 → 실행 파일 / 표 → Python 모델 | 결정적 코드 (LLM 없음) | 두 모델 불일치 = ②의 해석 오류 → ②로 반려 |
| ④ | RTL Generator | requirements + micro-arch → `.sv` | LLM | ⑥의 피드백으로 재생성 |
| ⑤ | TB Generator | requirements → TB + 벡터 파일 | 템플릿 + LLM 보조 | — |
| ⑥ | Verify Loop | RTL + TB + 기대값 → pass/fail + 불일치 목록 | iverilog (주), verilator (보조) | N회 초과 → 사람 에스컬레이션 |
| ⑦ | Quality Gate | RTL → lint/합성 리포트 | verilator `--lint-only -Wall`, yosys `synth; stat` | 경고 0 기준, 래치 추론 금지 |
| ⑧ | Report | 전체 → markdown/HTML | 결정적 코드 | — |

**LLM이 쓰는 곳은 ①②④⑤뿐이고, 판정(③⑥⑦)은 전부 결정적 도구가 한다.** 이게 신뢰성의 근거.

---

## 4. 검증 전략 (3중)

| 층 | 내용 | 기준 | 비고 |
|---|---|---|---|
| L1 공식 벡터 | STL `sweep.src` → 6종 기대값과 byte 비교 | 65536샘플 × 6종 100% 일치 | ITU 공식 산출물과 일치 = 가장 강한 근거 |
| L2 전수 검사 | 디코더 256 코드 전부, 인코더 16비트 입력 65536 전부를 골든 모델과 비교 | 100% 일치 | G.711은 입력 공간이 작아 전수 가능 |
| L3 스펙 표 적합성 | Table 1/2의 decision value 경계 ±1, reconstruction value, 부호/0 처리, `XOR 0x55` / 전체 반전 | 표 항목 전부 | 골든 모델이 틀려도 스펙 자체로 잡는 안전망 |
| 구조 | lint 경고, 래치, 조합 루프 | 0건 | yosys `check` |
| 인터페이스 | valid/ready 백프레셔, 리셋 직후 동작 | 랜덤 stall 테스트 통과 | 파이프라인 레지스터 포함 시 |

---

## 5. 기술 스택

| 영역 | 선택 | 근거 |
|---|---|---|
| 오케스트레이터 | Python CLI (이후 API 서버로 래핑) | 로컬에 python3 있음, 도구 호출 단순 |
| LLM | Claude API (`claude-opus-5`) | 스펙 해석·RTL 생성 품질 우선 |
| 시뮬레이터 | iverilog (주), verilator (lint/고속 sim) | 로컬 설치 확인됨 |
| 합성 | yosys | 로컬 설치 확인됨 |
| 골든 모델 | STL `g711.c` (gcc 빌드) + 스펙 표 Python 모델 | gcc 설치 확인됨 |
| TB 프레임워크 | SV 테스트벤치 + `$readmemh` 벡터 (Phase 1), cocotb (Phase 2 검토) | cocotb 미설치 |

---

## 6. 디렉터리 구조 (제안)

```
codec-agent/
├── specs/g711/                 # 스펙 PDF, 추출 결과
├── third_party/stl/            # openitu/STL (submodule, 수정 금지)
├── pipeline/                   # 오케스트레이터 (단계 ①~⑧)
├── golden/g711/                # 골든 모델 빌드/실행 래퍼, 표 기반 Python 모델
├── generated/g711/
│   ├── requirements.yaml
│   ├── rtl/                    # g711_{alaw,ulaw}_{enc,dec}.sv
│   ├── tb/
│   └── reports/
├── tests/                      # 파이프라인 자체 테스트
└── claudedocs/                 # 계획/분석 문서
```

---

## 7. 단계별 로드맵

각 Phase는 **종료 조건을 만족해야 다음으로 진행**. 기간은 조건 달성 기준이며 추정치를 두지 않음.

### Phase 0 — 수동 파일럿 (서비스 없이 사람+Claude Code로 1회 완주)
목적: 자동화할 절차와 실패 지점을 실측.
1. STL 클론, `g711demo` 빌드, CTest 6종 통과 확인
2. G.711 스펙 표 → `requirements.yaml` 수동 작성
3. 표 기반 Python 모델 작성 → STL과 65536 입력 교차 비교
4. RTL 4모듈 작성 → L1/L2/L3 검증 → lint/합성
5. 실패 유형 기록 (어디서 틀렸는지 = 자동화 루프가 잡아야 할 것)

**종료 조건**: L1·L2·L3 100% 일치, lint 경고 0, 실패 유형 목록 문서화.

### Phase 1 — 파이프라인 자동화 (G.711 end-to-end)
1. 단계 ③⑥⑦⑧ 결정적 도구 먼저 구현 (LLM 없이도 Phase 0 RTL로 돌아가야 함)
2. 단계 ④⑤ LLM 생성 + 피드백 루프 연결
3. 단계 ①② 연결 + 사람 승인 게이트
4. 동일 입력으로 반복 실행해 재현성 측정

**종료 조건**: 스펙 PDF → 검증 통과 RTL까지 사람 개입은 승인 게이트 1회만; 반복 실행 결과(통과율, 루프 횟수) 기록.

### Phase 2 — 일반화 검증 (두 번째 스펙: G.726 ADPCM)
- 상태(state)·적응 로직이 있어 G.711보다 어려움 → 파이프라인이 G.711 전용인지 판별하는 시험대
- STL `src/g726/`에 골든 모델·벡터 존재 (구조 확인 필요)
- hoangt/ASIC-Design이 G.726 MCAC 설계라 아키텍처 비교 대상으로 활용

**종료 조건**: G.726 L1 벡터 통과, 스펙별 코드 변경이 어디에 필요했는지 목록화.

### Phase 3 — 서비스화
- CLI → API/웹 UI, 작업 큐, 결과 리포트 페이지
- 멀티 스펙 지원(스펙 어댑터 인터페이스), 사내 스펙(Confluence/PDF) 입력
- 추적성 리포트를 인증/리뷰 산출물 형태로 정리

---

## 8. 리스크와 대응

| 리스크 | 영향 | 대응 |
|---|---|---|
| PDF 표 추출 오류 (G.711 표는 다단/각주 많음) | 요구사항 자체가 틀림 | ③에서 스펙 모델 vs STL 교차 비교로 검출, 사람 승인 게이트. **실측: G.711 PDF의 Table 1/2는 그림이라 텍스트 추출이 표를 오류 없이 누락** → ①은 페이지 렌더링+비전 판독 병행, 표는 인쇄된 구간 수·끝점으로 자체 검증 |
| 골든 모델과 스펙의 입력 표현 차이 (STL은 16비트 left-justified, 스펙은 13/14비트) | 비교 불일치 오판 | 인터페이스를 STL 기준으로 고정, 변환 규칙을 REQ로 명시 |
| LLM 피드백 루프 수렴 실패 | 무한 재시도, 비용 | 최대 반복 N회, 불일치 샘플을 구간별로 요약해 전달, 초과 시 에스컬레이션 |
| G.711에서만 동작하는 과적합 파이프라인 | 서비스 가치 없음 | Phase 2를 필수 관문으로 둠 |
| 외부 RTL 레포 라이선스 미명시 | 법적 리스크 | 코드 복사 금지, 구조 참고만; 생성 프롬프트에 외부 RTL 원문 투입 금지 |
| STL ITU-T GPL | 배포 조건 | 골든 모델은 서비스 내부 실행용으로만, 생성 RTL에 포함하지 않음 |
| "골든 모델과 일치 = 스펙 준수" 가정 | 참조 구현 버그 전파 | L3 스펙 표 직접 검사 유지 |

---

## 9. 결정이 필요한 사항

| 항목 | 기본안 | 대안 |
|---|---|---|
| RTL 언어 | SystemVerilog | Verilog-2001, VHDL |
| 인터페이스 | valid/ready + 출력 레지스터 1단 | 순수 조합, AXI-Stream |
| A/μ 구성 | 4개 개별 모듈 | `LAW` 선택 신호로 통합 (hoangt 방식) |
| 사람 승인 게이트 위치 | requirements.yaml 확정 시 1회 | RTL 최종본에도 추가 |
| 서비스 형태 | 사내 CLI → 내부 API | 처음부터 웹 서비스 |
| 대상 스펙 확장 순서 | G.711 → G.726 → 사내 스펙 | G.711 → 사내 스펙 바로 |

---

## 10. hls-rtl에서 도입한 것 (2026-09-17 반영)

`~/gitspace/hls-rtl`(Gamma Verification Workbench)은 같은 명제 — *"에이전트는 그럴듯한 RTL을 쓸 수 있지만 맞는지 판정할 수 없다"* — 를 감마 보정 코어로 먼저 구현한 프로젝트다. 서비스의 ③⑥⑦ 단계를 그 구조로 교체했다.

| hls-rtl 요소 | codec-agent 적용 | 위치 | 상태 |
|---|---|---|---|
| `improve.prove()` 단일 게이트, stage별 pass/fail/skip | lint → synthesize → simulate → formal (+equiv 정보용), `PROVEN / REJECTED / INCONCLUSIVE` | `pipeline/gate.py` | ✅ 적용 |
| "skip은 증거 부족이지 통과가 아니다" | gating skip ⇒ INCONCLUSIVE | `gate.py::Proof.verdict` | ✅ |
| `PROVES` / `DOES_NOT_PROVE` 문구를 판정에 동봉 | 동일, G.711 범위로 재작성 (PDF 원문 미대조 명시) | `gate.py` | ✅ |
| `failure_evidence()` — 값으로 된 실패 근거 | 첫 불일치 샘플(stim/got/exp) + 실패 property 이름 | `gate.py` | ✅ (Phase 1 LLM 피드백 입력) |
| SHA-256 증거 | 통과한 테스트의 기대 벡터 해시 + 후보 RTL 해시 기록 | `gate.py` → `runs/*.json` | ✅ |
| full-rate throughput guard | BP=0 테스트, `cycles ≤ N+3` | `tb_g711.sv`, `P_*` 테스트 | ✅ (기존 TB가 놓친 처리량 저하 검출) |
| `test_reset_state` | 적대적 리셋 환경(in_valid=1, out_ready=0) + negedge 해제 | `tb_g711.sv` | ✅ (race로 숨던 리셋 누락 검출) |
| black-box formal 불변식 (SymbiYosys) | A1–A6 + cover 3종, **datapath를 스펙 공식 참조모델과 unbounded 증명** (hls-rtl은 bounded) | `generated/g711/formal/` | ✅ |
| `mutate.py` 뮤테이션 커버리지 | 코덱 전용 연산자 추가(slice-shift, constant, mux-swap), 전 stage 실행 → 검출 매트릭스, 병렬 | `pipeline/mutate.py` | ✅ |
| `equiv.py` LEC | 후보 ≡ 베이스라인 판정(정보용) + 등가 뮤턴트 분류. `equiv_make`가 등가 뮤턴트를 증명 못해 **miter + sat -tempinduct로 교체** | `gate.py::stage_equiv` | ✅ |
| L0 pytest (골든 고정) | 스펙 표 anchor, 단조성, 대칭성, STL 일치, 벡터 manifest | `tests/test_golden.py` | ✅ |
| 루프 결과 분류 `PROVEN_CHANGE / ORACLE_CONFLICT / NO_CHANGE / EXHAUSTED` | Phase 1 LLM 루프 설계에 채택 (equiv가 NO_CHANGE 판별) | 본 문서 §3 ⑥ | 📋 설계만 |
| CLAUDE.md "hard-won rules" | 실제로 겪은 함정 10건 기록 | `CLAUDE.md` | ✅ |

**도입하지 않은 것 (사유)**

| 요소 | 사유 |
|---|---|
| SystemC 3번째 모델 | STL(C) + spec_model(Python) + RTL로 이미 독립 구현 3개 |
| 웹 UI / Hermes Gateway / KB(BM25) / Graph RAG | Phase 3 범위. 지금 넣으면 검증 대상 없는 인프라 |
| cocotb | iverilog TB로 충분, 추가 의존성 불필요 (필요 시 Phase 2 재검토) |
| house style 린터 | 규칙을 만들 코드베이스 관습이 아직 없음 |
| Verilator line coverage | 입력 공간 전수 + mutation으로 대체; TB가 iverilog라 계측 경로가 다름 |

**Phase 1 루프 설계 변경**: ⑥ Verify Loop는 `gate.prove(candidate)`를 호출하고, 반려 시 `failure_evidence()`만 생성기에 전달한다. 루프 종료는 boolean이 아니라 위 4개 결과 중 하나로 보고한다 — 생성기가 베이스라인으로 되돌아가 "통과"하는 경우(NO_CHANGE)를 수렴으로 착각하지 않기 위해서.
