import { readFileSync, readdirSync } from 'node:fs'
import { join, relative, resolve } from 'node:path'
import * as ts from 'typescript'
import { describe, expect, it } from 'vitest'
import { DE } from './de'

// English is the key and German the translation (ai-trainer-ops#268). Two
// kinds of text reach a German screen in English, and this test finds both:
// words written straight into the markup or a text attribute instead of
// through `t()`, and a `t()`, `setError()` or `new Error()` message that has no
// German entry. #218, #222 and #223 were each one such leak, found by hand.

const SRC = resolve(__dirname, '..')

/** Pages that carry their own German text: the privacy page and the legal notice. */
const OWN_GERMAN = new Set(['pages/PrivacyPage.tsx', 'pages/LegalNoticePage.tsx'])

/** Words that read the same in both languages: names, units, abbreviations. */
const SAME_IN_BOTH = new Set(
  [
    'Train', 'Like', 'a', 'Pro', 'Strava', 'intervals.icu', 'Intervals.icu', 'Garmin', 'Wahoo', 'Zwift',
    'Gemini', 'OpenAI', 'FTP', 'TSS', 'CTL', 'ATL', 'TSB', 'VO₂', 'VO2', 'max', 'ml', 'kg', 'min', 'km',
    'h', 'W', 'bpm', 'DOI', 'Code', 'optional', 'Fitness', 'Form',
  ].map((word) => word.toLowerCase()),
)

/** Examples that read the same in both languages: an address, key formats, a place, a URL. */
const SAME_TEXT = new Set(['you@example.com', 'AIza…', 'ABCD-EFGH-JK', 'Freiburg', 'trainlikea.pro/curve'])

/** Text an admin types to confirm a deletion: it must stay as written. */
const TYPED_PHRASES = new Set(['pages/AdminPage.tsx:delete'])

/** The attributes a reader sees or hears. */
const TEXT_ATTRIBUTES = new Set(['title', 'placeholder', 'aria-label', 'alt', 'label'])

function sources(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) return sources(path)
    return /\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name) && !path.endsWith(join('i18n', 'de.ts'))
      ? [path]
      : []
  })
}

/** Whether ``text`` has a word that is not the same in both languages. */
function hasWords(text: string): boolean {
  if (SAME_TEXT.has(text)) return false
  return text
    .split(/[^\p{L}\p{N}₂.]+/u)
    .map((word) => word.replace(/\.$/, ''))
    .some((word) => /\p{L}{2,}/u.test(word) && !SAME_IN_BOTH.has(word.toLowerCase()))
}

/** String literals in value position: the attribute itself, or what a condition or fallback yields. */
function valueLiterals(node: ts.Expression): ts.StringLiteralLike[] {
  if (ts.isStringLiteralLike(node)) return [node]
  if (ts.isParenthesizedExpression(node)) return valueLiterals(node.expression)
  if (ts.isConditionalExpression(node)) return [...valueLiterals(node.whenTrue), ...valueLiterals(node.whenFalse)]
  if (
    ts.isBinaryExpression(node) &&
    [ts.SyntaxKind.BarBarToken, ts.SyntaxKind.QuestionQuestionToken].includes(node.operatorToken.kind)
  ) {
    return [...valueLiterals(node.left), ...valueLiterals(node.right)]
  }
  return []
}

function calleeName(node: ts.CallExpression | ts.NewExpression): string {
  const callee = node.expression
  if (ts.isIdentifier(callee)) return callee.text
  if (ts.isPropertyAccessExpression(callee)) return callee.name.text
  return ''
}

/** What a parsed source leaks, as "file:line text". */
function leaksOf(source: ts.SourceFile, file: string): string[] {
  const found: string[] = []
  const at = (node: ts.Node, text: string) =>
    found.push(`${file}:${source.getLineAndCharacterOfPosition(node.getStart()).line + 1} ${text}`)
  const visit = (node: ts.Node) => {
    if (!OWN_GERMAN.has(file)) {
      if (ts.isJsxText(node)) {
        const text = node.text.replace(/\s+/g, ' ').trim()
        if (hasWords(text) && !TYPED_PHRASES.has(`${file}:${text}`)) at(node, text)
      }
      if (ts.isJsxAttribute(node) && TEXT_ATTRIBUTES.has(node.name.getText()) && node.initializer) {
        const value = ts.isJsxExpression(node.initializer) ? node.initializer.expression : node.initializer
        for (const literal of value ? valueLiterals(value) : []) {
          if (hasWords(literal.text)) at(literal, `${node.name.getText()}="${literal.text}"`)
        }
      }
    }
    if ((ts.isCallExpression(node) || ts.isNewExpression(node)) && node.arguments?.length) {
      const name = calleeName(node)
      const first = node.arguments[0]
      if (
        ['t', 'setError', 'Error'].includes(name) &&
        ts.isStringLiteralLike(first) &&
        first.text.trim().split(/\s+/).length >= 2 &&
        !(first.text in DE)
      ) {
        at(first, `${name}("${first.text}") has no German entry`)
      }
    }
    ts.forEachChild(node, visit)
  }
  visit(source)
  return found
}

function parse(text: string, path: string): ts.SourceFile {
  return ts.createSourceFile(path, text, ts.ScriptTarget.Latest, true, path.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS)
}

const leaks = (path: string) => leaksOf(parse(readFileSync(path, 'utf8'), path), relative(SRC, path))

describe('no English for a German screen (ai-trainer-ops#268)', () => {
  // Parses every source file: about a second alone, 14 s in a coverage run
  // beside the other files, where vitest's 5 s default stopped it.
  it('finds no text outside t() and no message without a German entry', () => {
    expect(sources(SRC).flatMap(leaks)).toEqual([])
  }, 60_000)

  it('catches the leaks it was written for', () => {
    const probe = (text: string) => leaksOf(parse(text, 'probe.tsx'), 'probe.tsx')
    expect(probe('const A = () => <p>Hello there</p>')).toHaveLength(1)
    expect(probe('const A = () => <button title="Expert mode ON" />')).toHaveLength(1)
    expect(probe("const A = () => <input placeholder={on ? 'Your name' : 'Name'} />")).toHaveLength(2)
    expect(probe("setError('Failed to send this')")).toHaveLength(1)
    expect(probe('const A = () => <p>{t("Back")} FTP · 250 W</p>')).toEqual([])
    expect(probe("const A = () => <input placeholder={sport === 'strength' ? t('Sets') : t('Notes')} />")).toEqual([])
  })
})
