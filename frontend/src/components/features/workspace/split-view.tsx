"use client";

import * as React from "react";

import { DocumentOutline, type OutlineNode } from "@/components/features/workspace/document-outline";
import { QuestionnairePanel } from "@/components/features/workspace/questionnaire-panel";
import type { Citation, Requirement } from "@/lib/api/types";

const MIN_PANE = 22;
const MAX_PANE = 60;

/**
 * The reviewer workbench: source on one side, answers on the other.
 *
 * The two panes share `activeCitation`, which is the whole point of the split
 * — clicking a citation chip on the answer side scrolls and highlights the
 * source on the document side without a round trip or a lost scroll position.
 */
export function SplitView({
  workspaceId,
  documentTitle,
  outline,
  requirements,
}: {
  workspaceId: string;
  documentTitle: string;
  outline: OutlineNode[];
  requirements: Requirement[];
}) {
  const [activeCitation, setActiveCitation] = React.useState<Citation | null>(null);
  const [leftPercent, setLeftPercent] = React.useState(34);
  const containerRef = React.useRef<HTMLDivElement>(null);
  const draggingRef = React.useRef(false);

  const onPointerMove = React.useCallback((event: PointerEvent) => {
    if (!draggingRef.current || !containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const raw = ((event.clientX - rect.left) / rect.width) * 100;
    // In RTL the left edge of the container is the *end* of the layout, so the
    // measured percentage has to be inverted or the divider tracks backwards.
    const isRtl = getComputedStyle(containerRef.current).direction === "rtl";
    const percent = isRtl ? 100 - raw : raw;
    setLeftPercent(Math.min(MAX_PANE, Math.max(MIN_PANE, percent)));
  }, []);

  React.useEffect(() => {
    const stop = () => {
      draggingRef.current = false;
      document.body.style.userSelect = "";
    };
    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", stop);
    return () => {
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", stop);
    };
  }, [onPointerMove]);

  return (
    <div ref={containerRef} className="flex h-full min-h-0">
      <div style={{ width: `${leftPercent}%` }} className="min-w-0 shrink-0">
        <DocumentOutline
          nodes={outline}
          activeCitation={activeCitation}
          documentTitle={documentTitle}
        />
      </div>

      <div
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize panes"
        tabIndex={0}
        onPointerDown={() => {
          draggingRef.current = true;
          // Without this, dragging selects text across both panes.
          document.body.style.userSelect = "none";
        }}
        onKeyDown={(e) => {
          // Keyboard resize: a pointer-only divider is unusable for anyone
          // working without a mouse.
          if (e.key === "ArrowLeft") setLeftPercent((v) => Math.max(MIN_PANE, v - 2));
          if (e.key === "ArrowRight") setLeftPercent((v) => Math.min(MAX_PANE, v + 2));
        }}
        className="group relative w-px shrink-0 cursor-col-resize bg-border focus-visible:outline-none"
      >
        <div className="absolute inset-y-0 -inset-x-1 transition-colors group-hover:bg-primary/30 group-focus-visible:bg-primary/50" />
      </div>

      <div className="min-w-0 flex-1">
        <QuestionnairePanel
          workspaceId={workspaceId}
          requirements={requirements}
          activeCitation={activeCitation}
          onSelectCitation={setActiveCitation}
        />
      </div>
    </div>
  );
}
