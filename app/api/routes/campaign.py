import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.db import get_db  # Updated import to use unified async db module
from app.models.campaign import Campaign, Message
from app.schemas.campaign import CampaignCreate, CampaignResponse, CampaignUpdate

router = APIRouter()
logger = logging.getLogger(__name__)


@router.post("/campaigns/", response_model=CampaignResponse)
async def create_campaign(campaign_data: CampaignCreate, db: Session = Depends(get_db)):
    """
    Create a new campaign using Pydantic schema for input data
    """
    try:
        logger.info(f"Creating new campaign: {campaign_data.title}")

        # Create campaign
        db_campaign = Campaign(
            title=campaign_data.title,
            description=campaign_data.description,
            scheduled_at=campaign_data.scheduled_at,
        )
        db.add(db_campaign)
        db.flush()  # Get campaign ID

        # Create messages with explicit status specification
        for message_data in campaign_data.messages:
            db_message = Message(
                campaign_id=db_campaign.id,
                recipient=message_data.recipient,
                content=message_data.content,
                status=message_data.status,  # Explicit status specification
            )
            db.add(db_message)

        db.commit()
        db.refresh(db_campaign)

        logger.info(f"Campaign created successfully with ID: {db_campaign.id}")
        return db_campaign

    except Exception as e:
        logger.error(f"Error creating campaign: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to create campaign: {str(e)}")


@router.get("/campaigns/", response_model=list[CampaignResponse])
async def get_campaigns(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """
    Get list of campaigns
    """
    try:
        logger.info(f"Fetching campaigns with skip={skip}, limit={limit}")
        campaigns = db.query(Campaign).offset(skip).limit(limit).all()
        return campaigns
    except Exception as e:
        logger.error(f"Error fetching campaigns: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch campaigns: {str(e)}")


@router.get("/campaigns/{campaign_id}", response_model=CampaignResponse)
async def get_campaign(campaign_id: int, db: Session = Depends(get_db)):
    """
    Get specific campaign by ID
    """
    try:
        logger.info(f"Fetching campaign with ID: {campaign_id}")
        campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")
        return campaign
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching campaign {campaign_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch campaign: {str(e)}")


@router.put("/campaigns/{campaign_id}", response_model=CampaignResponse)
async def update_campaign(
    campaign_id: int, campaign_data: CampaignUpdate, db: Session = Depends(get_db)
):
    """
    Update existing campaign
    """
    try:
        logger.info(f"Updating campaign with ID: {campaign_id}")
        campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")

        # Update only provided fields
        for field, value in campaign_data.dict(exclude_unset=True).items():
            setattr(campaign, field, value)

        db.commit()
        db.refresh(campaign)

        logger.info(f"Campaign {campaign_id} updated successfully")
        return campaign

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating campaign {campaign_id}: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to update campaign: {str(e)}")


@router.delete("/campaigns/{campaign_id}")
async def delete_campaign(campaign_id: int, db: Session = Depends(get_db)):
    """
    Delete campaign
    """
    try:
        logger.info(f"Deleting campaign with ID: {campaign_id}")
        campaign = db.query(Campaign).filter(Campaign.id == campaign_id).first()
        if not campaign:
            raise HTTPException(status_code=404, detail="Campaign not found")

        db.delete(campaign)
        db.commit()

        logger.info(f"Campaign {campaign_id} deleted successfully")
        return {"message": "Campaign deleted successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting campaign {campaign_id}: {str(e)}")
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to delete campaign: {str(e)}")
