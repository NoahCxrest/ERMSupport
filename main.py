import discord
from discord.ext import commands
import os
import logging
import asyncio
import aiohttp
import time
import json
from typing import Dict, List, Optional
from dataclasses import dataclass
from pathlib import Path

# Configuration
@dataclass
class BotConfig:
    """Configuration class for the bot."""
    token: str
    prefix: str = "!"
    config_path: Path = Path("config.json")
    cogs_path: Path = Path("Cogs")

    @classmethod
    def from_env(cls) -> "BotConfig":
        """Create config from environment variables."""
        from dotenv import load_dotenv
        load_dotenv()
        
        token = os.getenv("TOKEN")
        if not token:
            raise ValueError("Bot token not found in environment variables")
        
        return cls(token=token)

class BotLogger:
    """Handles logging configuration for the bot."""
    
    @staticmethod
    def setup(name: str, level: int = logging.INFO) -> logging.Logger:
        """Configure and return a logger instance."""
        logger = logging.getLogger(name)
        
        if not logger.handlers:  # Prevent duplicate handlers
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.setLevel(level)
        
        return logger

class DiscordBot(commands.AutoShardedBot):
    """Enhanced Discord bot with improved error handling and configuration management."""
    
    def __init__(self) -> None:
        """Initialize the bot with default settings."""
        self.config = BotConfig.from_env()
        self.logger = BotLogger.setup(__name__)
        
        intents = discord.Intents.default()
        intents.message_content = True
        
        super().__init__(
            command_prefix=self.config.prefix,
            intents=intents,
            help_command=None,
            chunk_guilds_at_startup=False
        )
        
        self.session: Optional[aiohttp.ClientSession] = None
        self.is_ready = asyncio.Event()
        self.commands_cache: Dict[str, List[str]] = {}
        self._startup_time: Optional[float] = None
        
        # Register error handlers
        self.tree.error(self.on_app_command_error)
        
    async def setup_hook(self) -> None:
        """Initialize async components of the bot."""
        self.session = aiohttp.ClientSession()
        self._startup_time = time.time()
        
        # Load configuration first
        await self.load_config()
        # Then load extensions
        await self.load_all_extensions()
    
    async def load_config(self) -> None:
        """Load bot configuration from JSON file."""
        try:
            if self.config.config_path.exists():
                config_data = json.loads(self.config.config_path.read_text())
                # Update config with file values while preserving required fields
                for key, value in config_data.items():
                    if hasattr(self.config, key):
                        setattr(self.config, key, value)
                self.logger.info("Configuration loaded successfully")
        except Exception as e:
            self.logger.error(f"Failed to load configuration: {e}")
    
    async def load_all_extensions(self) -> None:
        """Load all extensions from the cogs directory."""
        self.logger.info("Loading extensions...")
        
        extension_tasks = []
        
        # Load regular cogs
        if self.config.cogs_path.exists():
            for file in self.config.cogs_path.glob("*.py"):
                if file.stem != "__init__":
                    extension_tasks.append(
                        self.load_extension(f"{self.config.cogs_path.stem}.{file.stem}")
                    )
        
        # Try to load jishaku if available
        try:
            extension_tasks.append(self.load_extension("jishaku"))
        except commands.ExtensionError as e:
            self.logger.warning(f"Failed to load jishaku: {e}")
        
        # Load all extensions concurrently
        results = await asyncio.gather(*extension_tasks, return_exceptions=True)
        
        # Log any errors that occurred during loading
        for result, task in zip(results, extension_tasks):
            if isinstance(result, Exception):
                self.logger.error(f"Failed to load extension: {task} - {result}")
    
    async def cache_commands(self) -> None:
        """Cache all application commands for the help system."""
        if not self.session:
            return
            
        try:
            url = f"https://discord.com/api/v10/applications/{self.user.id}/commands"
            async with self.session.get(
                url,
                headers={"Authorization": f"Bot {self.http.token}"},
                params={"with_localizations": "true"}
            ) as response:
                response.raise_for_status()
                commands_data = await response.json()
                
            # Organize commands by cog
            for cmd_data in commands_data:
                cmd = self.get_command(cmd_data['name'])
                cog_name = cmd.cog_name if cmd else 'No Category'
                cmd_desc = f"</{cmd_data['name']}:{cmd_data['id']}> - {cmd_data['description']}"
                self.commands_cache.setdefault(cog_name, []).append(cmd_desc)
                
            self.logger.info(f"Cached {len(commands_data)} commands")
            
        except Exception as e:
            self.logger.error(f"Failed to cache commands: {e}")
    
    async def on_ready(self) -> None:
        """Handle bot ready event."""
        await self.wait_until_ready()
        
        self.logger.info(f"Logged in as {self.user.name}#{self.user.discriminator}")
        
        await self.cache_commands()
        await self.change_presence(activity=discord.Game(name="with ERM Systems"))
        
        if self._startup_time:
            elapsed = (time.time() - self._startup_time) * 1000
            self.logger.info(f"Bot is ready! Startup took {elapsed:.2f}ms")
        
        self.is_ready.set()
    
    async def on_command_error(self, ctx: commands.Context, error: Exception) -> None:
        """Handle command errors."""
        error_mapping = {
            commands.CommandNotFound: (
                lambda: self.logger.warning(f"Command not found: {ctx.message.content}")
            ),
            commands.MissingRequiredArgument: (
                lambda: ctx.reply("You're missing some required arguments.")
            ),
            commands.NoPrivateMessage: (
                lambda: ctx.send("This command cannot be used in direct messages.")
            ),
            commands.CommandOnCooldown: (
                lambda: ctx.reply(
                    f"This command is on cooldown. Try again in {error.retry_after:.2f}s."
                )
            ),
            commands.MissingPermissions: (
                lambda: ctx.reply("You don't have permission to use this command. 😔")
            )
        }
        
        # Handle specific error types
        handler = error_mapping.get(type(error))
        if handler:
            await handler()
            return
            
        # Don't handle errors with custom handlers
        if hasattr(ctx.command, 'on_error'):
            return
            
        # Handle unexpected errors
        self.logger.error(f"Unexpected error in command {ctx.command}: {error}", exc_info=error)
        await ctx.reply(f"An unexpected error occurred: {error}")
    
    async def on_app_command_error(
        self,
        interaction: discord.Interaction,
        error: discord.app_commands.AppCommandError
    ) -> None:
        """Handle application command errors."""
        self.logger.error(f"Error in slash command: {error}", exc_info=error)
        await interaction.response.send_message(
            f"An error occurred while processing this command: {error}",
            ephemeral=True
        )
    
    async def close(self) -> None:
        """Clean up resources when shutting down."""
        if self.session:
            await self.session.close()
        await super().close()

async def main() -> None:
    """Main entry point for the bot."""
    bot = DiscordBot()
    
    try:
        async with bot:
            await bot.start(bot.config.token)
    except asyncio.CancelledError:
        bot.logger.info("Bot shutdown initiated")
    except Exception as e:
        bot.logger.critical(f"Fatal error occurred: {e}", exc_info=e)
    finally:
        await bot.close()

if __name__ == "__main__":
    asyncio.run(main())
