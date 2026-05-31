from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import date
from ..database import get_db
from ..models.review import DailyReview
from ..schemas.review import DailyReviewCreate, DailyReviewResponse, DailyReviewListResponse
from ..services.ai_service import ai_generator
from ..services.market_state_service import get_current_market_state

router = APIRouter(prefix="/reviews", tags=["reviews"])


@router.get("", response_model=DailyReviewListResponse)
def list_reviews(
    page: int = Query(1, ge=1),
    page_size: int = Query(30, ge=1, le=90),
    db: Session = Depends(get_db),
):
    q = db.query(DailyReview).order_by(DailyReview.date.desc())
    total = q.count()
    items = q.offset((page - 1) * page_size).limit(page_size).all()
    return DailyReviewListResponse(
        items=[DailyReviewResponse.model_validate(r) for r in items],
        total=total,
    )


@router.get("/latest", response_model=DailyReviewResponse)
def get_latest_review(db: Session = Depends(get_db)):
    review = db.query(DailyReview).order_by(DailyReview.date.desc()).first()
    if not review:
        raise HTTPException(status_code=404, detail="No reviews found")
    return DailyReviewResponse.model_validate(review)


@router.get("/{review_date}", response_model=DailyReviewResponse)
def get_review_by_date(review_date: date, db: Session = Depends(get_db)):
    review = db.query(DailyReview).filter(DailyReview.date == review_date).first()
    if not review:
        raise HTTPException(status_code=404, detail=f"No review for {review_date}")
    return DailyReviewResponse.model_validate(review)


@router.post("", response_model=DailyReviewResponse, status_code=201)
def create_or_update_review(payload: DailyReviewCreate, db: Session = Depends(get_db)):
    existing = db.query(DailyReview).filter(DailyReview.date == payload.date).first()

    # Generate AI narrative if none provided
    if not payload.market_summary:
        payload.market_summary = ai_generator.generate_market_review(
            market_phase=payload.market_phase or "neutral",
            profit_effect=payload.profit_effect_score,
            loss_effect=payload.loss_effect_score,
            strong_sectors=payload.strong_sectors or [],
            dangerous_sectors=payload.dangerous_sectors or [],
            emotional_temperature=payload.emotional_temperature,
            review_date=payload.date,
        )

    if existing:
        for field, val in payload.model_dump(exclude_none=True).items():
            setattr(existing, field, val)
        db.commit()
        db.refresh(existing)
        return DailyReviewResponse.model_validate(existing)

    review = DailyReview(**payload.model_dump())
    db.add(review)
    db.commit()
    db.refresh(review)
    return DailyReviewResponse.model_validate(review)


@router.post("/generate-today", response_model=DailyReviewResponse, status_code=201)
def generate_today_review(db: Session = Depends(get_db)):
    """Auto-generate today's review from current market state."""
    state = get_current_market_state(db)
    today = date.today()

    summary = ai_generator.generate_market_review(
        market_phase=state.market_phase,
        profit_effect=state.profit_effect_score,
        loss_effect=state.loss_effect_score,
        strong_sectors=state.strong_sectors,
        dangerous_sectors=state.dangerous_sectors,
        emotional_temperature=state.emotional_temperature,
        review_date=today,
    )

    payload = DailyReviewCreate(
        date=today,
        market_phase=state.market_phase,
        profit_effect_score=state.profit_effect_score,
        loss_effect_score=state.loss_effect_score,
        emotional_temperature=state.emotional_temperature,
        suggested_position_level=state.suggested_position_level,
        strong_sectors=state.strong_sectors,
        dangerous_sectors=state.dangerous_sectors,
        active_sectors=[s.sector_name for s in state.active_sectors],
        tomorrow_watchlist=[c.stock_code for c in state.weak_to_strong_candidates[:5]],
        market_summary=summary,
    )

    existing = db.query(DailyReview).filter(DailyReview.date == today).first()
    if existing:
        for field, val in payload.model_dump(exclude_none=True).items():
            setattr(existing, field, val)
        db.commit()
        db.refresh(existing)
        return DailyReviewResponse.model_validate(existing)

    review = DailyReview(**payload.model_dump())
    db.add(review)
    db.commit()
    db.refresh(review)
    return DailyReviewResponse.model_validate(review)
