import { setRequestLocale } from "next-intl/server";

import type { OutlineNode } from "@/components/features/workspace/document-outline";
import { SplitView } from "@/components/features/workspace/split-view";
import type { Requirement } from "@/lib/api/types";

/**
 * Placeholder tender.
 *
 * Replaced by `GET /workspaces/{id}` plus the extracted compliance matrix once
 * the requirement-extraction service lands. The bilingual mix is deliberate:
 * a GCC tender that is Arabic prose with English standard references is the
 * normal case, and a layout that only ever sees one script hides its own bugs.
 */
const OUTLINE: OutlineNode[] = [
  { id: "1", label: "1. Instructions to Bidders", page: 1, depth: 0 },
  { id: "1.1", label: "1.1 Submission Deadline", page: 2, depth: 1 },
  { id: "1.2", label: "1.2 Bid Validity", page: 3, depth: 1 },
  { id: "2", label: "2. الشروط العامة", page: 8, depth: 0 },
  { id: "2.1", label: "2.1 الضمان البنكي", page: 9, depth: 1 },
  { id: "3", label: "3. Technical Requirements", page: 14, depth: 0 },
  { id: "3.2", label: "3.2 Information Security", page: 17, depth: 1 },
  { id: "3.3", label: "3.3 Support and Maintenance", page: 21, depth: 1 },
  { id: "4", label: "4. Bill of Quantities", page: 30, depth: 0 },
];

const REQUIREMENTS: Requirement[] = [
  {
    ref: "3.2.14",
    text: "The Contractor shall hold a valid ISO/IEC 27001 certification for the full duration of the contract, and shall provide evidence of the certificate on request.",
    section_path: "3. Technical Requirements > 3.2 Information Security",
    is_mandatory: true,
  },
  {
    ref: "3.2.15",
    text: "The Contractor shall describe its information security incident response process, including notification timescales.",
    section_path: "3. Technical Requirements > 3.2 Information Security",
    is_mandatory: true,
  },
  {
    ref: "3.3.02",
    text: "The Contractor shall provide 24/7 technical support with a maximum response time of four (4) hours for critical incidents.",
    section_path: "3. Technical Requirements > 3.3 Support and Maintenance",
    is_mandatory: true,
  },
  {
    ref: "2.1.04",
    text: "يلتزم المقاول بتقديم ضمان بنكي بنسبة ٥٪ من قيمة العقد خلال ثلاثين يوماً من تاريخ الترسية.",
    section_path: "2. الشروط العامة > 2.1 الضمان البنكي",
    is_mandatory: true,
  },
  {
    ref: "5.1.07",
    text: "The Contractor shall describe any prior experience decommissioning nuclear facilities.",
    section_path: "5. Company Experience",
    is_mandatory: false,
  },
];

export default async function TenderWorkspacePage({
  params,
}: {
  params: Promise<{ locale: string; tenderId: string }>;
}) {
  const { locale, tenderId } = await params;
  setRequestLocale(locale);

  return (
    <SplitView
      workspaceId={tenderId}
      documentTitle="MOH/2026/IT/0114 — كراسة الشروط"
      outline={OUTLINE}
      requirements={REQUIREMENTS}
    />
  );
}
