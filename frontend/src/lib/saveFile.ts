/** Hand a file the backend just built to the browser's downloader.
 *
 *  An anchor, deliberately, and not `window.open`: the popup would be opened
 *  after the `await` of the build request, which is outside the click's user
 *  activation, and browsers block those. To the user a blocked popup and a
 *  failed download look identical — nothing happens — so the one entry point
 *  that used `window.open` could silently do nothing in a real browser while
 *  passing in a headless one, where the popup blocker is not enforced.
 *
 *  `download` is the other half. Without it the anchor navigates rather than
 *  saves, so any response the browser decides to render inline — a proxy that
 *  drops Content-Disposition, a format it recognises — would replace the app
 *  with the file's own text. It also pins the saved name to the name the
 *  server used, which is what the page has already shown the user. */
export function saveFile(url: string, fileName?: string) {
  const anchor = document.createElement('a')
  anchor.href = url
  // An empty value means "use the last segment of the URL", i.e. the server's
  // own name — still a save, never a navigation.
  anchor.download = fileName ?? ''
  anchor.rel = 'noopener'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
}

/** The name to save under: the file the server wrote, as the URL spells it. */
export function fileNameFromUrl(url: string): string {
  const last = url.split('?')[0].split('/').filter(Boolean).pop()
  return last ? decodeURIComponent(last) : 'download'
}
