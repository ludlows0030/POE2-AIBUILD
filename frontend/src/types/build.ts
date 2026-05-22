/** POE2 BD Agent — TypeScript 类型定义 */

export interface GenerateRequest {
  user_request: string;
  game_version?: string;
}

export interface SkillGem {
  name: string;
  support_gems: string[];
  role: "main_dps" | "mobility" | "aura" | "debuff" | "weapon_swap";
}

export interface AuraReservation {
  name: string;
  spirit_cost: number;
}

export interface SkillGems {
  active: SkillGem[];
  spirit_reservation?: AuraReservation[];
}

export interface PassiveTree {
  nodes: string[];
  keystones?: string[];
  mastery_choices?: Record<string, string>;
}

export interface Equipment {
  [slot: string]: string;
}

export interface ValidationResult {
  passed: boolean;
  errors: string[];
  warnings: string[];
  suggestions?: string[];
  score?: number;
}

export interface DamageBreakdown {
  average_hit?: number;
  estimated_dps?: number;
  assumptions?: Record<string, number>;
}

export interface BuildCard {
  id?: string;
  build_name: string;
  core_concept: string;
  class: string;
  ascendancy: string;
  ascendancy_nodes: string[];
  skill_gems: SkillGems;
  passive_tree: PassiveTree;
  equipment: Equipment;
  key_mechanics: string[];
  playstyle_notes: string;
  estimated_dps: number | string;
  estimated_budget_divines: number;
  budget_tier: string;
  confidence: number;
  strengths: string[];
  weaknesses: string[];
  validation: ValidationResult;
  damage_breakdown: DamageBreakdown;
  reference_builds_count: number;
  game_version: string;
}

export interface BuildListItem {
  id: string;
  build_name: string;
  core_skill: string;
  confidence: number;
  game_version: string;
  created_at: string;
}

export interface BuildListResponse {
  builds: BuildListItem[];
  total: number;
}
