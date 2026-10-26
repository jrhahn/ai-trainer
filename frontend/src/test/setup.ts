import '@testing-library/jest-dom/vitest'

// jsdom does not implement scrollIntoView
window.HTMLElement.prototype.scrollIntoView = () => {}
// nor scrolling the window
window.scrollTo = () => {}
