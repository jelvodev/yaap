import { type ReactNode, createContext, useContext } from "react";

/**
 * The section a part is shown in. A part renders its own SectionHeader; inside a section the header carries the
 * section's title and its tabs below it, while the part's title names the tab its badge and reset belong to.
 */
export interface SectionFrame {
  title: string;
  /** The tab bar, when the section has more than one part at this level. */
  tabs: ReactNode | null;
}

export const SectionFrameContext = createContext<SectionFrame | null>(null);

export function useSectionFrame(): SectionFrame | null {
  return useContext(SectionFrameContext);
}
