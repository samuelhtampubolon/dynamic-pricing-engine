"""Unit Economics and Cohort Lifetime Value (LTV) / Customer Acquisition Cost (CAC) Engine."""

from __future__ import annotations
from typing import Any, Dict, List
from src.lakehouse import lakehouse


class UnitEconomicsService:
    """Computes customer cohort unit economics, LTV/CAC ratios, and CAC payback periods."""

    @staticmethod
    def calculate_ltv(
        arpu_monthly: float,
        gross_margin_pct: float,
        monthly_churn_rate: float,
    ) -> float:
        """Governing Equation: LTV = (ARPU * Gross Margin %) / Monthly Churn Rate."""
        if monthly_churn_rate <= 0.0001:
            monthly_churn_rate = 0.01
        return round((arpu_monthly * gross_margin_pct) / monthly_churn_rate, 2)

    @staticmethod
    def calculate_ltv_cac_ratio(ltv_usd: float, cac_usd: float) -> float:
        """Governing Equation: LTV/CAC Ratio = LTV / CAC."""
        if cac_usd <= 0:
            return 0.0
        return round(ltv_usd / cac_usd, 2)

    @staticmethod
    def calculate_payback_months(
        cac_usd: float,
        arpu_monthly: float,
        gross_margin_pct: float,
    ) -> float:
        """Governing Equation: Payback Months = CAC / (ARPU * Gross Margin %)."""
        denom = arpu_monthly * gross_margin_pct
        if denom <= 0:
            return 999.0
        return round(cac_usd / denom, 1)

    def get_tenant_cohorts(self, tenant_id: str) -> Dict[str, Any]:
        """Retrieve tenant-isolated cohort economics from gold_cohort_economics table."""
        with lakehouse.get_connection(read_only=True) as conn:
            rows = conn.execute("""
                SELECT 
                    cohort_id,
                    tenant_id,
                    arpu_usd,
                    gross_margin_pct,
                    churn_rate,
                    cac_usd,
                    ltv_usd,
                    ltv_cac_ratio
                FROM gold_cohort_economics
                WHERE tenant_id = ?
                ORDER BY cohort_id ASC
            """, [tenant_id]).fetchall()

        if not rows:
            return {
                "tenant_id": tenant_id,
                "cohorts": [],
                "aggregate_ltv_to_cac": 0.0,
                "aggregate_cac_usd": 0.0,
                "aggregate_ltv_usd": 0.0,
                "avg_payback_months": 0.0,
            }

        cohorts: List[Dict[str, Any]] = []
        tot_ltv = 0.0
        tot_cac = 0.0
        tot_payback = 0.0

        for r in rows:
            c_id, t_id, arpu, gm, churn, cac, ltv, ratio = r
            payback = self.calculate_payback_months(cac, arpu, gm)
            tot_ltv += ltv
            tot_cac += cac
            tot_payback += payback
            cohorts.append({
                "cohort_id": c_id,
                "tenant_id": t_id,
                "arpu_usd": arpu,
                "gross_margin_pct": gm,
                "churn_rate": churn,
                "cac_usd": cac,
                "ltv_usd": ltv,
                "ltv_cac_ratio": ratio,
                "payback_months": payback,
            })

        n = len(cohorts)
        avg_cac = tot_cac / n if n > 0 else 0.0
        avg_ltv = tot_ltv / n if n > 0 else 0.0
        agg_ratio = avg_ltv / avg_cac if avg_cac > 0 else 0.0
        avg_payback = tot_payback / n if n > 0 else 0.0

        return {
            "tenant_id": tenant_id,
            "cohorts": cohorts,
            "aggregate_ltv_to_cac": round(agg_ratio, 2),
            "aggregate_cac_usd": round(avg_cac, 2),
            "aggregate_ltv_usd": round(avg_ltv, 2),
            "avg_payback_months": round(avg_payback, 1),
        }


unit_economics_service = UnitEconomicsService()
