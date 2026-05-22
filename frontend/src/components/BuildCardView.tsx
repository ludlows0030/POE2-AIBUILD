import { useState } from "react";
import type { BuildCard } from "../types/build";

function ConfidenceBadge({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color = pct >= 80 ? "green" : pct >= 50 ? "yellow" : "red";
  return <span className={`badge confidence ${color}`}>{pct}% confidence</span>;
}

function BudgetBadge({ tier, divines }: { tier: string; divines: number }) {
  return (
    <span className="badge budget">
      {tier} tier (~{divines}d)
    </span>
  );
}

export function BuildCardView({ build }: { build: BuildCard }) {
  const [tab, setTab] = useState<"overview" | "skills" | "equipment" | "details">("overview");

  const tabs = [
    { key: "overview" as const, label: "Overview" },
    { key: "skills" as const, label: "Skills" },
    { key: "equipment" as const, label: "Equipment" },
    { key: "details" as const, label: "Details" },
  ];

  return (
    <div className="build-card">
      {/* Header */}
      <div className="build-header">
        <div>
          <h2>{build.build_name}</h2>
          <span className="class-tag">
            {build.ascendancy
              ? `${build.ascendancy} ${build.class}`
              : build.class}
          </span>
          <ConfidenceBadge value={build.confidence} />
          <BudgetBadge tier={build.budget_tier} divines={build.estimated_budget_divines} />
        </div>
        {build.estimated_dps && (
          <div className="dps-display">
            <span className="dps-number">{build.estimated_dps}</span>
            <span className="dps-label">est. DPS</span>
          </div>
        )}
      </div>

      {/* Core concept */}
      <p className="core-concept">{build.core_concept}</p>

      {/* Tabs */}
      <div className="tabs">
        {tabs.map((t) => (
          <button
            key={t.key}
            className={`tab ${tab === t.key ? "active" : ""}`}
            onClick={() => setTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="tab-content">
        {tab === "overview" && (
          <OverviewTab build={build} />
        )}
        {tab === "skills" && (
          <SkillsTab build={build} />
        )}
        {tab === "equipment" && (
          <EquipmentTab build={build} />
        )}
        {tab === "details" && (
          <DetailsTab build={build} />
        )}
      </div>
    </div>
  );
}

function OverviewTab({ build }: { build: BuildCard }) {
  return (
    <div className="tab-overview">
      <div className="overview-grid">
        <div className="ov-section">
          <h4>Ascendancy</h4>
          <ol>
            {build.ascendancy_nodes?.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ol>
        </div>
        <div className="ov-section">
          <h4>Key Mechanics</h4>
          <ul>
            {build.key_mechanics?.map((m, i) => (
              <li key={i}>{m}</li>
            ))}
          </ul>
        </div>
        <div className="ov-section">
          <h4>Passive Tree</h4>
          <p className="muted">
            {build.passive_tree?.nodes?.length || "?"} nodes allocated
          </p>
          {build.passive_tree?.keystones?.length ? (
            <ul>
              {build.passive_tree.keystones.map((k, i) => (
                <li key={i} className="keystone">{k}</li>
              ))}
            </ul>
          ) : null}
        </div>
      </div>

      {(build.strengths?.length > 0 || build.weaknesses?.length > 0) && (
        <div className="sw-grid">
          {build.strengths?.length > 0 && (
            <div>
              <h4 className="text-green">Strengths</h4>
              <ul>
                {build.strengths.map((s, i) => (
                  <li key={i}>{s}</li>
                ))}
              </ul>
            </div>
          )}
          {build.weaknesses?.length > 0 && (
            <div>
              <h4 className="text-red">Weaknesses</h4>
              <ul>
                {build.weaknesses.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {build.playstyle_notes && (
        <div className="playstyle-box">
          <h4>How to Play</h4>
          <p>{build.playstyle_notes}</p>
        </div>
      )}
    </div>
  );
}

function SkillsTab({ build }: { build: BuildCard }) {
  const skills = build.skill_gems;
  return (
    <div className="tab-skills">
      {skills?.active?.map((skill, i) => (
        <div key={i} className="skill-row">
          <div className="skill-name">
            <strong>{skill.name}</strong>
            <span className="skill-role">{skill.role}</span>
          </div>
          <div className="support-gems">
            {skill.support_gems?.map((gem, j) => (
              <span key={j} className="support-chip">{gem}</span>
            ))}
          </div>
        </div>
      ))}

      {skills?.spirit_reservation?.length ? (
        <div className="spirit-section">
          <h4>Spirit Reservation</h4>
          {skills.spirit_reservation.map((aura, i) => (
            <div key={i} className="spirit-row">
              <span>{aura.name}</span>
              <span className="spirit-cost">{aura.spirit_cost} Spirit</span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function EquipmentTab({ build }: { build: BuildCard }) {
  const order = [
    "Weapon", "Offhand", "Helmet", "BodyArmour",
    "Gloves", "Boots", "Amulet", "Ring1", "Ring2", "Belt",
  ];
  const eq = build.equipment;

  return (
    <div className="tab-equipment">
      {order.map((slot) => {
        const item = eq?.[slot];
        if (!item) return null;
        return (
          <div key={slot} className="eq-row">
            <span className="eq-slot">{slot}</span>
            <span className="eq-item">{item}</span>
          </div>
        );
      })}
    </div>
  );
}

function DetailsTab({ build }: { build: BuildCard }) {
  const v = build.validation;
  const dmg = build.damage_breakdown;

  return (
    <div className="tab-details">
      <div className="detail-section">
        <h4>Validation</h4>
        <span className={`badge ${v?.passed ? "green" : "red"}`}>
          {v?.passed ? "PASSED" : "FAILED"}
        </span>
        {v?.score != null && <span> Score: {v.score}/100</span>}
        {v?.errors?.map((e, i) => (
          <div key={i} className="msg error">[ERROR] {e}</div>
        ))}
        {v?.warnings?.map((w, i) => (
          <div key={i} className="msg warn">[WARN] {w}</div>
        ))}
        {v?.suggestions?.map((s, i) => (
          <div key={i} className="msg info">[TIP] {s}</div>
        ))}
      </div>

      {dmg && (dmg.average_hit || dmg.estimated_dps) && (
        <div className="detail-section">
          <h4>Damage Breakdown</h4>
          <table className="dmg-table">
            <tbody>
              {dmg.average_hit != null && (
                <tr><td>Average Hit</td><td>{dmg.average_hit}</td></tr>
              )}
              {dmg.estimated_dps != null && (
                <tr><td>Estimated DPS</td><td>{dmg.estimated_dps}</td></tr>
              )}
              {dmg.assumptions &&
                Object.entries(dmg.assumptions).map(([k, v]) => (
                  <tr key={k}><td className="muted">{k}</td><td>{v}</td></tr>
                ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="detail-section meta">
        <span>Game version: {build.game_version}</span>
        <span>Reference builds: {build.reference_builds_count}</span>
      </div>
    </div>
  );
}
