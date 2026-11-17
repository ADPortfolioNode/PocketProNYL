import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import Header from './Header';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let container;
let root;
let scrollTo;
beforeEach(() => {
  scrollTo = jest.fn();
  window.scrollTo = scrollTo;
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

const renderHeader = (initialEntries = ['/']) => {
  act(() => root.render(
    <MemoryRouter initialEntries={initialEntries}>
      <Header startupStatus={null} />
    </MemoryRouter>
  ));
};

test('header carries the top anchor so the page starts at the top', () => {
  renderHeader();
  const header = container.querySelector('header.magazine-header');
  expect(header).not.toBeNull();
  expect(header.id).toBe('top');
});

test('page scrolls to top on mount and on route change', () => {
  renderHeader(['/']);
  expect(scrollTo).toHaveBeenCalledWith(0, 0);
  scrollTo.mockClear();
  const resourcesLink = Array.from(container.querySelectorAll('a'))
    .find((a) => a.textContent.trim() === 'Resources');
  expect(resourcesLink).not.toBeUndefined();
  act(() => {
    resourcesLink.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
  });
  expect(scrollTo).toHaveBeenCalledWith(0, 0);
});
