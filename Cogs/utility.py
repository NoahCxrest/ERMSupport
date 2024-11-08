from __future__ import annotations

import asyncio
from typing import Dict, Any, Optional
from functools import lru_cache
import discord
from discord.ext import commands
import psutil
from pathlib import Path
import json

class Utility(commands.Cog):
    """Utility commands with optimized resource usage."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._config: Optional[Dict[str, Any]] = None
        self._memory_process = psutil.Process()
        self._embed_color = discord.Color.from_rgb(43, 45, 49)
    
    @property
    def config(self) -> Dict[str, Any]:
        """Lazy load and cache configuration."""
        if self._config is None:
            self._config = self.load_config()
        return self._config
    
    @staticmethod
    @lru_cache(maxsize=1)
    def load_config() -> Dict[str, Any]:
        """Load and cache configuration from JSON file."""
        config_path = Path('./config.json')
        if not config_path.exists():
            return {}
        
        return json.loads(config_path.read_text(encoding='utf-8'))
    
    @commands.hybrid_command(
        name="ping",
        with_app_command=True,
        description="Get the bot's latency"
    )
    async def ping(self, ctx: commands.Context) -> None:
        """Fast latency check command."""
        embed = discord.Embed(
            description=f'Pong: {self.bot.latency * 1000:.0f}ms',
            color=self._embed_color
        )
        await ctx.reply(embed=embed)
    
    @commands.hybrid_command(
        name="help",
        with_app_command=True,
        description="Get a list of commands"
    )
    async def get_commands(self, ctx: commands.Context) -> None:
        """Efficient command list display."""
        try:
            commands_by_cog = self.bot.commands_cache
            if not commands_by_cog:
                await ctx.send(
                    embed=discord.Embed(
                        description="Cronus is still loading commands; please try again in a few seconds.",
                        color=self._embed_color
                    )
                )
                return
            
            embed = discord.Embed(title="Command List", color=self._embed_color)
            
            # Sort once and build fields efficiently
            for cog_name, cog_commands in sorted(commands_by_cog.items()):
                embed.add_field(
                    name=cog_name,
                    value="\n".join(cog_commands),
                    inline=False
                )
            
            await ctx.send(embed=embed)
            
        except Exception as e:
            self.bot.logger.error(f"Error in help command: {e}", exc_info=True)
            await ctx.send("An error occurred while fetching the command list. Please try again later.")
    
    @commands.hybrid_command(
        name="about",
        with_app_command=True,
        description="Learn about Cronus"
    )
    async def about(self, ctx: commands.Context) -> None:
        """Optimized about command with cached resource usage."""
        embed = discord.Embed(
            title="About Cronus",
            description=(
                "Cronus is a Discord bot created by "
                "[Noah](https://discord.com/users/459374864067723275) "
                "to help with the management of the ERM Systems Discord server."
            ),
            color=self._embed_color
        )
        
        if self.bot.user.avatar:
            embed.set_thumbnail(url=self.bot.user.avatar.url)
        
        # Gather system stats concurrently
        memory_usage = self._memory_process.memory_info().rss / (1024 ** 2)
        cpu_percent = psutil.cpu_percent(interval=0.1)  # Quick CPU check
        
        embed.add_field(name="RAM Usage", value=f"{memory_usage:.2f} MB", inline=True)
        embed.add_field(name="CPU Usage", value=f"{cpu_percent}%", inline=True)
        embed.add_field(name="Loaded Cogs", value=str(len(self.bot.cogs)), inline=True)
        
        await ctx.send(embed=embed)

async def setup(bot: commands.Bot) -> None:
    """Set up the Utility cog."""
    await bot.add_cog(Utility(bot))
