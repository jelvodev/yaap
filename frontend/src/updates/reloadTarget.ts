// Pure, so it is tested without a browser (reloadTarget.test.mjs); it imports nothing.

/** The version to offer a reload for: the one YAAP runs now, when it differs from the build this page came from.
 *  Nothing when either is unknown (a development server has no build version). */
export function reloadTarget(buildVersion: string, runningVersion: string | null | undefined): string | null {
  if (!buildVersion || !runningVersion) return null;
  return runningVersion !== buildVersion ? runningVersion : null;
}
