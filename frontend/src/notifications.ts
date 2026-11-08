/** Browser notifications for when YAAP needs you while its tab is not visible. */

/** Ask once, on a user action such as sending a message; the browser remembers the answer. */
export function askForNotifications(): void {
  if (typeof Notification === "undefined" || Notification.permission !== "default") return;
  void Notification.requestPermission().catch(() => undefined);
}

export function notifyIfHidden(title: string, body: string, tag?: string): void {
  if (typeof Notification === "undefined" || Notification.permission !== "granted" || !document.hidden) return;
  try {
    const notification = new Notification(title, { body, tag });
    notification.onclick = () => {
      window.focus();
      notification.close();
    };
  } catch {
    // Some browsers allow notifications only from a service worker; then there is simply none.
  }
}
