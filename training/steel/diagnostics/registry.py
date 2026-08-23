from __future__ import annotations

import pandas as pd


INTERPRETATION_LIMIT = "예측 연관 후보이며 공정 원인·설비 고장의 증거가 아님"


FEATURE_MAPPINGS: dict[str, tuple[str, str, str, str, str, str]] = {
    "sm_plant": ("제강·연주", "공장 구분", "제강·연주 공장/라인", "운영 맥락", "CONTEXT", "공장별 조건 차이 확인"),
    "steel_grade": ("제강", "강종", "강종·성분 설계 기준", "제품 사양", "CONTEXT", "강종별 기준 조건 확인"),
    "steel_usage": ("제강", "용도", "제품 용도·품질 기준", "제품 사양", "CONTEXT", "용도별 품질 기준 확인"),
    "delta_ferrite": ("제강", "용강 성분·조직", "성분 분석 및 용강 품질 관리", "품질 특성", "INDIRECT", "Delta Ferrite 관련 조건 확인"),
    "ingre_cr": ("제강", "성분", "Cr 성분 분석·원료 투입", "성분 계측", "INDIRECT", "Cr 성분 조건 확인"),
    "ingre_ni": ("제강", "성분", "Ni 성분 분석·원료 투입", "성분 계측", "INDIRECT", "Ni 성분 조건 확인"),
    "ingre_s": ("제강", "성분", "S 성분 분석·정련", "성분 계측", "INDIRECT", "S 성분 조건 확인"),
    "cc_gubun": ("연주", "연주기 구분", "연주기", "설비 맥락", "CONTEXT", "연주기별 조건 차이 확인"),
    "tundish_temp": ("연주", "Tundish", "Tundish 온도 계측·제어", "온도", "CONTROLLABLE", "Tundish 온도 조건 우선 확인"),
    "mlac_ratio": ("연주", "Mold level control", "Mold Level 자동제어 계통", "제어 성능", "CONTROLLABLE", "MLAC 운전·계측 상태 확인"),
    "slab_gubun": ("연주", "주편 구분", "주편 분류·이력", "제품 맥락", "CONTEXT", "주편 구분별 조건 확인"),
    "slab_grind": ("주편 정정", "표면 정정", "주편 Grinding 공정", "처리 이력", "CONTROLLABLE", "주편 정정 코드와 작업 이력 확인"),
    "furnace_no": ("열연", "가열로", "가열로 호기", "설비 맥락", "CONTEXT", "가열로 호기별 조건 차이 확인"),
    "f_jangip_gubun": ("열연", "가열로 장입", "가열로 장입 운영", "장입 방식", "CONTROLLABLE", "장입 구분과 기준 준수 확인"),
    "f_jangip_temp": ("열연", "가열로 장입", "장입 온도 계측", "온도", "CONTROLLABLE", "장입 온도 조건 우선 확인"),
    "f_bfg": ("열연", "가열로 연소", "BFG 공급·유량 계통", "연료 유량", "CONTROLLABLE", "BFG 공급 조건 확인"),
    "f_cog": ("열연", "가열로 연소", "COG 공급·유량 계통", "연료 유량", "CONTROLLABLE", "COG 공급 조건 확인"),
    "f_ldg": ("열연", "가열로 연소", "LDG 공급·유량 계통", "연료 유량", "CONTROLLABLE", "LDG 공급 조건 확인"),
    "f_pre_temp": ("열연", "가열로 예열대", "가열로 예열대", "온도", "CONTROLLABLE", "예열대 온도 조건 확인"),
    "f_heat_temp": ("열연", "가열로 가열대", "가열로 가열대", "온도", "CONTROLLABLE", "가열대 온도 조건 우선 확인"),
    "f_sock_temp": ("열연", "가열로 균열대", "가열로 균열대", "온도", "CONTROLLABLE", "균열대 온도 조건 우선 확인"),
    "f_pre_interval": ("열연", "가열로 예열대", "가열로 예열대 이송·추적", "체류시간", "CONTROLLABLE", "예열대 체류시간 확인"),
    "f_heat_interval": ("열연", "가열로 가열대", "가열로 가열대 이송·추적", "체류시간", "CONTROLLABLE", "가열대 체류시간 확인"),
    "f_sock_interval": ("열연", "가열로 균열대", "가열로 균열대 이송·추적", "체류시간", "CONTROLLABLE", "균열대 체류시간 확인"),
    "f_ext_time": ("열연", "가열로 추출", "가열로 추출 스케줄·추적", "시간 맥락", "CONTEXT", "추출 시간대별 운전 조건 확인"),
    "hr_thick": ("열연", "압연 치수", "열연 두께 계측 계통", "치수", "INDIRECT", "열연 두께 조건 확인"),
    "hr_width": ("열연", "압연 치수", "열연 폭 계측 계통", "치수", "INDIRECT", "열연 폭 조건 확인"),
    "rm4_temp": ("열연", "조압연", "RM4 온도 계측 계통", "온도", "CONTROLLABLE", "RM4 온도 조건 우선 확인"),
    "rm_pitch": ("열연", "조압연", "조압연 Mill 이송·Pitch 계통", "압연 조건", "CONTROLLABLE", "조압연 Pitch 조건 확인"),
    "slab_width": ("열연", "소재 치수", "Slab 폭 계측·소재 이력", "입력 치수", "INDIRECT", "Slab 폭과 열연 폭 관계 확인"),
}


def build_feature_registry(features: list[str]) -> pd.DataFrame:
    rows: list[dict[str, str]] = []
    for feature in features:
        mapping = FEATURE_MAPPINGS.get(feature)
        if mapping is None:
            process_stage, sub_process, equipment, signal_role, controllability, message = (
                "미분류",
                "미분류",
                "도메인 검토 필요",
                "미분류",
                "UNKNOWN",
                f"{feature}의 공정·설비 매핑 검토",
            )
            status = "REVIEW_REQUIRED"
        else:
            process_stage, sub_process, equipment, signal_role, controllability, message = mapping
            status = "MAPPED"
        if status == "REVIEW_REQUIRED":
            candidate_kind = "DOMAIN_REVIEW_REQUIRED"
            review_route = "변수 정의와 현장 Tag 매핑부터 검토"
        elif controllability == "CONTEXT":
            candidate_kind = "CONTEXT_ONLY"
            review_route = "동일 제품·운전 조건으로 층화 비교"
        elif controllability == "INDIRECT":
            candidate_kind = "MEASUREMENT_OR_MATERIAL_CONTEXT"
            review_route = "계측 정의·소재 이력·품질 기준 확인"
        else:
            candidate_kind = "PROCESS_OR_MEASUREMENT_POINT"
            review_route = "공정 Trend와 계측·제어 로그 우선 확인"
        rows.append(
            {
                "feature": feature,
                "process_stage": process_stage,
                "sub_process": sub_process,
                "equipment_candidate": equipment,
                "signal_role": signal_role,
                "controllability": controllability,
                "mapping_status": status,
                "candidate_kind": candidate_kind,
                "recommended_review_route": review_route,
                "automated_maintenance_ticket_eligible": False,
                "display_message_ko": message,
                "interpretation_limit": INTERPRETATION_LIMIT,
            }
        )
    return pd.DataFrame(rows)
