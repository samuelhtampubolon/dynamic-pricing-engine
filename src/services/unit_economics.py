"""Unit Economics and Cohort Lifetime Value (LTV) / Customer Acquisition Cost (CAC) Engine."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from src.config import settings
from src.lakehouse import lakehouse


class UnitEconomicsService:
    """Computes customer cohort unit economics, LTV/CAC ratios, and CAC payback periods."""

    @staticmethod
    def calculate_ltv(
        arpu_monthly: float,
        gross_margin_pct: float,
        monthly_churn_rate: float,
    ) -> float:
        """Governing Equation: LTV = (ARPU * Gross Margin %) / Monthly Churn Rate.

        This is the undiscounted geometric series sum_{t>=1} m (1-c)^(t-1) = m / c.
        """
        if monthly_churn_rate <= 0.0001:
            monthly_churn_rate = 0.01
        return round((arpu_monthly * gross_margin_pct) / monthly_churn_rate, 2)

    @staticmethod
    def monthly_discount_rate(annual_rate: float) -> float:
        """d_m = (1 + d_a)^(1/12) - 1 (compound-equivalent monthly rate)."""
        return (1.0 + annual_rate) ** (1.0 / 12.0) - 1.0

    @staticmethod
    def calculate_discounted_ltv(
        arpu_monthly: float,
        gross_margin_pct: float,
        monthly_churn_rate: float,
        monthly_discount_rate: float,
    ) -> float:
        """Discounted LTV with end-of-month cash flows:

            LTV_d = sum_{t>=1} m (1-c)^(t-1) / (1+d)^t = m / (c + d)

        Reduces to m / c when d = 0.
        """
        denom = monthly_churn_rate + monthly_discount_rate
        if denom <= 0:
            return 0.0
        return round(arpu_monthly * gross_margin_pct / denom, 2)

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

    @staticmethod
    def calculate_churn_adjusted_payback(
        cac_usd: float,
        arpu_monthly: float,
        gross_margin_pct: float,
        monthly_churn_rate: float,
    ) -> Optional[float]:
        """Months n for a cohort's survival-weighted contribution to recover CAC:

            sum_{t=1}^{n} m (1-c)^(t-1) = m (1 - (1-c)^n) / c = CAC
            =>  n = ln(1 - CAC c / m) / ln(1 - c)

        Returns ``None`` when CAC is never recovered (CAC >= m / c, i.e. LTV <= CAC).
        """
        m = arpu_monthly * gross_margin_pct
        c = monthly_churn_rate
        if m <= 0:
            return None
        if c <= 0:
            return round(cac_usd / m, 2)
        x = 1.0 - cac_usd * c / m
        if x <= 0:
            return None
        return round(math.log(x) / math.log(1.0 - c), 2)

    @staticmethod
    def expected_lifetime_months(monthly_churn_rate: float) -> float:
        """E[T] = 1 / c for a geometric lifetime."""
        return 1.0 / monthly_churn_rate if monthly_churn_rate > 0 else float("inf")

    @staticmethod
    def annual_churn_rate(monthly_churn_rate: float) -> float:
        """Annualised churn: 1 - (1 - c)^12."""
        return 1.0 - (1.0 - monthly_churn_rate) ** 12

    @staticmethod
    def customer_half_life_months(monthly_churn_rate: float) -> float:
        """Months until half of the cohort has churned: ln(0.5) / ln(1 - c)."""
        if monthly_churn_rate <= 0:
            return float("inf")
        return math.log(0.5) / math.log(1.0 - monthly_churn_rate)

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
                    ltv_cac_ratio,
                    COALESCE(new_customers, 1)
                FROM gold_cohort_economics
                WHERE tenant_id = ?
                ORDER BY cohort_id ASC
            """, [tenant_id]).fetchall()

        annual_rate = settings.ANNUAL_DISCOUNT_RATE
        d = self.monthly_discount_rate(annual_rate)
        empty = {
            "tenant_id": tenant_id,
            "cohorts": [],
            "aggregate_ltv_to_cac": 0.0,
            "aggregate_cac_usd": 0.0,
            "aggregate_ltv_usd": 0.0,
            "aggregate_discounted_ltv_usd": 0.0,
            "aggregate_discounted_ltv_to_cac": 0.0,
            "avg_payback_months": 0.0,
            "total_new_customers": 0,
            "annual_discount_rate": annual_rate,
        }
        if not rows:
            return empty

        cohorts: List[Dict[str, Any]] = []
        w_ltv = w_dltv = w_cac = w_payback = 0.0
        total_n = 0
        for c_id, t_id, arpu, gm, churn, cac, ltv, ratio, n_cust in rows:
            n_cust = int(n_cust)
            payback = self.calculate_payback_months(cac, arpu, gm)
            dltv = self.calculate_discounted_ltv(arpu, gm, churn, d)
            total_n += n_cust
            w_ltv += n_cust * ltv
            w_dltv += n_cust * dltv
            w_cac += n_cust * cac
            w_payback += n_cust * payback
            cohorts.append({
                "cohort_id": c_id,
                "tenant_id": t_id,
                "new_customers": n_cust,
                "arpu_usd": arpu,
                "gross_margin_pct": gm,
                "churn_rate": churn,
                "cac_usd": cac,
                "ltv_usd": ltv,
                "ltv_cac_ratio": ratio,
                "payback_months": payback,
                "monthly_contribution_usd": round(arpu * gm, 2),
                "discounted_ltv_usd": dltv,
                "discounted_ltv_cac_ratio": self.calculate_ltv_cac_ratio(dltv, cac),
                "churn_adjusted_payback_months": self.calculate_churn_adjusted_payback(cac, arpu, gm, churn),
                "expected_lifetime_months": round(self.expected_lifetime_months(churn), 2),
                "annual_churn_rate": round(self.annual_churn_rate(churn), 4),
                "customer_half_life_months": round(self.customer_half_life_months(churn), 2),
            })

        # Customer-weighted aggregates: sum(n LTV) / sum(n CAC), not a mean of ratios.
        avg_ltv = w_ltv / total_n
        avg_dltv = w_dltv / total_n
        avg_cac = w_cac / total_n
        return {
            "tenant_id": tenant_id,
            "cohorts": cohorts,
            "aggregate_ltv_to_cac": round(avg_ltv / avg_cac, 2) if avg_cac > 0 else 0.0,
            "aggregate_cac_usd": round(avg_cac, 2),
            "aggregate_ltv_usd": round(avg_ltv, 2),
            "aggregate_discounted_ltv_usd": round(avg_dltv, 2),
            "aggregate_discounted_ltv_to_cac": round(avg_dltv / avg_cac, 2) if avg_cac > 0 else 0.0,
            "avg_payback_months": round(w_payback / total_n, 1),
            "total_new_customers": total_n,
            "annual_discount_rate": annual_rate,
        }


unit_economics_service = UnitEconomicsService()
