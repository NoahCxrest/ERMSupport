from __future__ import annotations

import asyncio
from typing import List, Optional
import discord
from discord.ui import View, Button
from discord import Interaction, ButtonStyle, Embed

class BasePaginatorView(View):
    """Base class for paginator views with common functionality."""
    def __init__(self, timeout: Optional[float] = 180.0):
        super().__init__(timeout=timeout)
        self.message: Optional[discord.Message] = None
    
    async def on_timeout(self) -> None:
        """Handle view timeout by disabling all buttons."""
        if self.message:
            for item in self.children:
                if isinstance(item, Button):
                    item.disabled = True
            await self.message.edit(view=self)

class TagListPaginator(BasePaginatorView):
    """Efficient paginator for tag lists with minimal memory usage."""
    
    __slots__ = ("bot", "pages", "current_page", "ctx")
    
    def __init__(self, bot: discord.Client, pages: List[Embed], timeout: Optional[float] = 180.0):
        super().__init__(timeout=timeout)
        self.bot = bot
        self.pages = pages
        self.current_page = 0
        self.ctx: Optional[discord.ApplicationContext] = None
        
        # Disable navigation buttons if there's only one page
        if len(pages) <= 1:
            self.prev_button.disabled = True
            self.next_button.disabled = True
    
    async def send_page(self, page_number: int) -> None:
        """Send or edit message with the current page."""
        embed = self.pages[page_number]
        
        if self.message is None:
            self.message = await self.ctx.send(embed=embed, view=self)
        else:
            await self.message.edit(embed=embed, view=self)
            
        # Update button states
        self.prev_button.disabled = page_number == 0
        self.next_button.disabled = page_number == len(self.pages) - 1
    
    @discord.ui.button(
        style=ButtonStyle.secondary,
        custom_id="prev_button",
        row=1,
        emoji="<:l_arrow:1169754353326903407>"
    )
    async def prev_button(self, interaction: Interaction, _: Button) -> None:
        await interaction.response.defer()
        self.current_page = max(0, self.current_page - 1)
        await self.send_page(self.current_page)
    
    @discord.ui.button(
        style=ButtonStyle.secondary,
        custom_id="next_button",
        row=1,
        emoji="<:arrow:1169695690784518154>"
    )
    async def next_button(self, interaction: Interaction, _: Button) -> None:
        await interaction.response.defer()
        self.current_page = min(len(self.pages) - 1, self.current_page + 1)
        await self.send_page(self.current_page)
    
    async def start(self, ctx: discord.ApplicationContext, *, wait: bool = False) -> Optional[TagListPaginator]:
        """Start the paginator."""
        self.ctx = ctx
        await self.send_page(self.current_page)
        if wait:
            await self.wait()
            return None
        return self

