"""
Cloudinary image storage service integration.
"""

from typing import Any, Optional

import cloudinary
import cloudinary.api
import cloudinary.uploader
from fastapi import UploadFile

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class CloudinaryService:
    """Service for Cloudinary image management"""

    def __init__(self):
        # Configure Cloudinary
        cloudinary.config(
            cloud_name=settings.CLOUDINARY_CLOUD_NAME,
            api_key=settings.CLOUDINARY_API_KEY,
            api_secret=settings.CLOUDINARY_API_SECRET,
            secure=True,
        )

    async def upload_image(
        self,
        file: UploadFile,
        folder: str = "smartsell",
        transformation: Optional[dict[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        """Upload image to Cloudinary"""

        try:
            # Read file content
            contents = await file.read()

            # Prepare upload options
            upload_options = {
                "folder": folder,
                "resource_type": "auto",
                "format": "webp",  # Convert to WebP for better compression
                "quality": "auto:good",
                "fetch_format": "auto",
            }

            # Add transformation if provided
            if transformation:
                upload_options["transformation"] = transformation
            else:
                # Default transformation for product images
                upload_options["transformation"] = [
                    {"width": 800, "height": 800, "crop": "limit"},
                    {"quality": "auto:good", "fetch_format": "auto"},
                ]

            # Upload to Cloudinary
            result = cloudinary.uploader.upload(contents, **upload_options)

            logger.info(f"Image uploaded to Cloudinary: {result.get('public_id')}")
            return result

        except Exception as e:
            logger.error(f"Cloudinary upload error: {e}")
            return None

    async def delete_image(self, public_id: str) -> bool:
        """Delete image from Cloudinary"""

        try:
            result = cloudinary.uploader.destroy(public_id)

            if result.get("result") == "ok":
                logger.info(f"Image deleted from Cloudinary: {public_id}")
                return True
            else:
                logger.warning(f"Failed to delete image: {public_id}")
                return False

        except Exception as e:
            logger.error(f"Cloudinary delete error: {e}")
            return False

    async def get_image_info(self, public_id: str) -> Optional[dict[str, Any]]:
        """Get image information"""

        try:
            result = cloudinary.api.resource(public_id)
            logger.info(f"Retrieved image info: {public_id}")
            return result

        except Exception as e:
            logger.error(f"Cloudinary get image info error: {e}")
            return None

    async def update_image(
        self, public_id: str, transformation: Optional[dict[str, Any]] = None, **kwargs
    ) -> Optional[dict[str, Any]]:
        """Update image metadata or transformation"""

        try:
            update_options = kwargs

            if transformation:
                update_options["transformation"] = transformation

            result = cloudinary.api.update(public_id, **update_options)
            logger.info(f"Image updated: {public_id}")
            return result

        except Exception as e:
            logger.error(f"Cloudinary update error: {e}")
            return None

    def generate_url(
        self, public_id: str, transformation: Optional[dict[str, Any]] = None, **kwargs
    ) -> str:
        """Generate optimized image URL"""

        try:
            url_options = {
                "secure": True,
                "quality": "auto:good",
                "fetch_format": "auto",
            }

            if transformation:
                url_options["transformation"] = transformation

            url_options.update(kwargs)

            url = cloudinary.utils.cloudinary_url(public_id, **url_options)[0]
            return url

        except Exception as e:
            logger.error(f"Cloudinary URL generation error: {e}")
            return ""

    def generate_thumbnail_url(self, public_id: str, width: int = 200, height: int = 200) -> str:
        """Generate thumbnail URL"""

        transformation = {
            "width": width,
            "height": height,
            "crop": "fill",
            "gravity": "center",
        }

        return self.generate_url(public_id, transformation=transformation)

    async def upload_multiple_images(
        self, files: list[UploadFile], folder: str = "smartsell"
    ) -> list[dict[str, Any]]:
        """Upload multiple images"""

        results = []

        for file in files:
            result = await self.upload_image(file, folder)
            if result:
                results.append(result)

        logger.info(f"Uploaded {len(results)} images to Cloudinary")
        return results

    async def create_image_archive(
        self, public_ids: list[str], archive_type: str = "zip"
    ) -> Optional[str]:
        """Create archive of multiple images"""

        try:
            result = cloudinary.utils.archive_url(
                public_ids=public_ids,
                resource_type="image",
                type="upload",
                format=archive_type,
            )

            logger.info(f"Created image archive with {len(public_ids)} images")
            return result

        except Exception as e:
            logger.error(f"Cloudinary archive creation error: {e}")
            return None

    async def get_usage_stats(self) -> Optional[dict[str, Any]]:
        """Get Cloudinary usage statistics"""

        try:
            result = cloudinary.api.usage()
            logger.info("Retrieved Cloudinary usage stats")
            return result

        except Exception as e:
            logger.error(f"Cloudinary usage stats error: {e}")
            return None

    def get_responsive_breakpoints(
        self,
        public_id: str,
        max_width: int = 1200,
        min_width: int = 300,
        bytes_step: int = 20000,
    ) -> dict[str, Any]:
        """Get responsive breakpoint URLs"""

        try:
            transformation = {
                "responsive_breakpoints": [
                    {
                        "create_derived": True,
                        "bytes_step": bytes_step,
                        "min_width": min_width,
                        "max_width": max_width,
                        "max_images": 5,
                    }
                ]
            }

            result = cloudinary.utils.cloudinary_url(public_id, transformation=transformation)

            return result

        except Exception as e:
            logger.error(f"Responsive breakpoints error: {e}")
            return {}

    async def auto_tag_image(self, public_id: str) -> Optional[dict[str, Any]]:
        """Auto-tag image using AI"""

        try:
            result = cloudinary.uploader.explicit(
                public_id,
                type="upload",
                categorization="google_tagging",
                auto_tagging=0.7,  # Confidence threshold
            )

            logger.info(f"Auto-tagged image: {public_id}")
            return result

        except Exception as e:
            logger.error(f"Auto-tagging error: {e}")
            return None
