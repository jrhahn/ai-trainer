import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { render } from '@testing-library/react'
import type { RenderResult } from '@testing-library/react'
import type { ReactElement } from 'react'

export function renderWithRouter(ui: ReactElement, { initialPath = '/' } = {}): RenderResult {
  return render(<MemoryRouter initialEntries={[initialPath]}>{ui}</MemoryRouter>)
}

export function renderWithRoute(
  element: ReactElement,
  path: string,
  initialPath: string
): RenderResult {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path={path} element={element} />
      </Routes>
    </MemoryRouter>
  )
}
