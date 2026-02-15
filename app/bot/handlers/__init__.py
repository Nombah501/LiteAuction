from aiogram import Router

from .bid_actions import router as bid_actions_router
from .create_auction import router as create_auction_router
from .emoji_tools import router as emoji_tools_router
from .feedback import router as feedback_router
from .guarantor import router as guarantor_router
from .inline_auction import router as inline_auction_router
from .moderation import router as moderation_router
from .points import router as points_router
from .publish_auction import router as publish_auction_router
from .start import router as start_router
from .trade_feedback import router as trade_feedback_router

router = Router(name="root")
router.include_router(start_router)
router.include_router(emoji_tools_router)
router.include_router(create_auction_router)
router.include_router(bid_actions_router)
router.include_router(inline_auction_router)
router.include_router(feedback_router)
router.include_router(guarantor_router)
router.include_router(points_router)
router.include_router(publish_auction_router)
router.include_router(trade_feedback_router)
router.include_router(moderation_router)

__all__ = ["router"]
