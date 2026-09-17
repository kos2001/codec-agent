import { createContext, useContext } from 'react'

/**
 * Korean / English UI text (adapted from hls-rtl).
 *
 * Keys are the English source string, so a missing translation renders English
 * instead of an id. Technical vocabulary is not translated: stage names
 * (`lint`, `formal`), verdicts (`PROVEN`), REQ-IDs, signal names and file paths are
 * the same tokens the CLI prints and the transcript logs.
 */

export type Lang = 'en' | 'ko'

const KO: Record<string, string> = {
  // shell
  'spec → RTL · the gate decides, not the model': 'spec → RTL · 판정은 모델이 아니라 게이트가',
  'Reload all': '전체 다시 읽기',
  'Export transcript…': 'Transcript 내보내기…',
  'Design navigator': '설계 탐색기',
  Properties: '속성',
  Transcript: 'Transcript',
  'Reset layout': '레이아웃 초기화',
  'Light theme': '밝은 테마',
  'Dark theme': '어두운 테마',
  'Run gate': '게이트 실행',
  'Running gate…': '게이트 실행 중…',
  'Run L0 tests': 'L0 테스트 실행',
  'Generate with Hermes…': 'Hermes로 생성…',
  'Mutation campaign…': '뮤테이션 캠페인…',
  'What is this?': '이게 뭔가요?',
  About: '정보',
  connected: '연결됨',
  offline: '연결 끊김',
  Reload: '다시 읽기',
  design: '설계',
  gate: '게이트',
  model: '모델',
  updated: '갱신',
  'loading…': '읽는 중…',
  follow: '따라가기',
  Copy: '복사',
  Clear: '지우기',
  'filter (tag or text)': '필터 (태그 또는 텍스트)',
  'No messages at this threshold. Run a flow from the toolbar.':
    '이 수준의 메시지가 없습니다. 툴바에서 flow를 실행하세요.',

  // overview
  'An agent can write plausible RTL. It cannot tell you whether the RTL is right.':
    '에이전트는 그럴듯한 RTL을 쓸 수 있다. 그 RTL이 맞는지는 말해주지 못한다.',
  'The proof chain': '증명 체인',
  'What this does not prove.': '이것이 증명하지 않는 것.',
  'Design under verification': '검증 대상 설계',
  'What the workbench adds': '워크벤치가 더하는 것',
  'Open sign-off →': 'Sign-off 열기 →',
  'Open generate →': 'Generate 열기 →',

  // views
  'Sign-off gate': 'Sign-off 게이트',
  'Stage results': 'Stage 결과',
  'Simulation tests': '시뮬레이션 테스트',
  'What PROVEN licenses you to say': 'PROVEN이 말해도 되는 것',
  'What it does not prove': '증명하지 않는 것',
  'Failure evidence': '실패 근거',
  'Requirements': '요구사항',
  'Oracle tests (L0)': 'Oracle 테스트 (L0)',
  'Excluded from scope': '범위 제외',
  'Mutation coverage': '뮤테이션 커버리지',
  'Detection by stage': 'Stage별 검출',
  'By fault class': '결함 유형별',
  Survivors: '생존 뮤턴트',
  'Generation loop (Hermes)': '생성 루프 (Hermes)',
  'Attempts': '시도',
  'Run history': '실행 이력',
  'Prompt sent to the model': '모델에 보낸 프롬프트',
  'Candidate RTL': '후보 RTL',
  'Committed RTL': '커밋된 RTL',
  Documents: '문서',
  'Select an object in the navigator.': '탐색기에서 객체를 고르세요.',
}

const DICT: Record<Lang, Record<string, string>> = { en: {}, ko: KO }

export const LangContext = createContext<{ lang: Lang; setLang: (l: Lang) => void }>({
  lang: 'en',
  setLang: () => {},
})

export function useLang() {
  return useContext(LangContext)
}

export function useT(): (en: string) => string {
  const { lang } = useLang()
  return (en: string) => DICT[lang][en] ?? en
}

export function readStoredLang(): Lang {
  try {
    const v = localStorage.getItem('cw-lang')
    if (v === 'ko' || v === 'en') return v
  } catch { /* private mode */ }
  return typeof navigator !== 'undefined' && navigator.language?.startsWith('ko') ? 'ko' : 'en'
}

export function storeLang(l: Lang) {
  try { localStorage.setItem('cw-lang', l) } catch { /* private mode */ }
}
