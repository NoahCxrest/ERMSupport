from __future__ import annotations

import json
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Tuple, List
from pathlib import Path
import os
from functools import lru_cache

import discord
from discord.utils import format_dt
from discord.ext import commands
from motor.motor_asyncio import AsyncIOMotorClient
import re
from dotenv import load_dotenv
from menus import TagListPaginator

load_dotenv()

class Support(commands.Cog):
    """Support cog for managing Sentry integration and tag commands."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._config: Optional[Dict[str, Any]] = None
        self._setup_database()
        self._embed_color = discord.Color.from_rgb(43, 45, 49)
        
    def _setup_database(self) -> None:
        """Initialize database connections and collections."""
        mongo_uri = os.getenv('MONGO_URI')
        if not mongo_uri:
            raise ValueError("MONGO_URI environment variable not set")
            
        self.client = AsyncIOMotorClient(mongo_uri)
        self.database = self.client["Cronus"]
        self.collection = self.database["threads"]
        self.tag_collection = self.database["tags"]
        self._create_indexes()
    
    @property
    def config(self) -> Dict[str, Any]:
        """Lazy load and cache configuration."""
        if self._config is None:
            self._config = self.load_config()
        return self._config
    
    @property
    def headers(self) -> Dict[str, str]:
        """Generate Sentry API headers."""
        return {"Authorization": f"Bearer {self.config['SENTRY_API_KEY']}"}
    
    @staticmethod
    @lru_cache(maxsize=1)
    def load_config() -> Dict[str, Any]:
        """Load and cache configuration from JSON file."""
        config_path = Path('./config.json')
        if not config_path.exists():
            return {}
        
        return json.loads(config_path.read_text(encoding='utf-8'))
    
    async def _fetch_issues(self, error_id: str) -> Optional[List[Dict[str, Any]]]:
        """Fetch issues from Sentry API."""
        url = (
            f"{self.config['SENTRY_API_URL']}/projects/"
            f"{self.config['SENTRY_ORGANIZATION_SLUG']}/"
            f"{self.config['PROJECT_SLUG']}/issues/"
        )
        query_params = {"query": f"error_id:{error_id}"}

        try:
            async with self.bot.session.get(
                url, 
                headers=self.headers, 
                params=query_params, 
                timeout=10
            ) as response:
                if response.status == 200:
                    json_data = await response.json()
                    self.bot.logger.info(f"Received JSON data: {json_data}")
                    return json_data
                self.bot.logger.error(f"Error response: {await response.json()}")
                return None
        except Exception as e:
            self.bot.logger.error(f"Error fetching issues: {e}")
            return None

    def _process_response(
        self, 
        issues: List[Dict[str, Any]]
    ) -> Optional[Tuple[str, str, bool, str]]:
        """Process the response from the Sentry API."""
        if not issues:
            self.bot.logger.warning("No issues found in response.")
            return None

        issue = issues[0]
        title = issue.get('title', 'Title not available')
        value = issue.get('metadata', {}).get('value', 'Value not available')
        handled = issue.get('isUnhandled', 'Handled information not available')
        last_seen = issue.get('lastSeen', 'Last seen not available')

        last_seen_dt = datetime.fromisoformat(
            last_seen.replace('Z', '+00:00')
        ).replace(tzinfo=timezone.utc)
        last_seen_formatted = format_dt(last_seen_dt, style='R')

        return title, value, handled, last_seen_formatted

    def _get_issue_url(self, json_data: List[Dict[str, Any]]) -> Optional[str]:
        """Generate Sentry issue URL from response data."""
        try:
            if json_data and isinstance(json_data, list) and 'id' in json_data[0]:
                issue_id = json_data[0]['id']
                return (
                    f"https://ermcorporation.sentry.io/issues/{issue_id}/"
                    "?environment=production&project=5919400"
                )
        except Exception as e:
            self.bot.logger.error(f"Error generating issue URL: {e}")
        return None

    async def _update_sentry_embed(
        self,
        message: discord.Message,
        title: str,
        value: str,
        handled: bool,
        last_seen: str,
        error_url: str
    ) -> None:
        """Update the Sentry issue embed with new information."""
        embed = discord.Embed(
            title=f"Sentry Issue: {title}", 
            color=self._embed_color
        )
        embed.add_field(name="Value", value=value, inline=False)
        embed.add_field(name="Unhandled", value=handled, inline=False)
        embed.add_field(name="Last Seen", value=last_seen, inline=False)
        embed.add_field(name="Sentry URL", value=error_url, inline=False)
        await message.edit(content=None, embed=embed)

    @commands.hybrid_command(
        name="sentry",
        description="Get a Sentry issue by error ID"
    )
    @commands.has_any_role('Support')
    async def sentry(self, ctx: commands.Context, error_id: str) -> None:
        """Fetch and display Sentry issue information."""
        loading = await ctx.reply(content="Fetching...")

        async def _fetch_with_retry(
            error_id: str,
            max_attempts: int = 4,
            initial_retry_interval: float = 2.0
        ) -> bool:
            for attempt in range(1, max_attempts + 1):
                issues = await self._fetch_issues(error_id)
                if issues:
                    issue_data = self._process_response(issues)
                    if issue_data:
                        error_url = self._get_issue_url(issues)
                        await self._update_sentry_embed(
                            loading,
                            *issue_data,
                            error_url
                        )
                        return True

                retry_interval = initial_retry_interval * 1.3 ** (attempt - 1)
                await loading.edit(
                    content=(
                        f"No matching issues found for error ID: {error_id}... "
                        f"**Retrying in {retry_interval:.1f} seconds**."
                    )
                )
                await asyncio.sleep(retry_interval)
            return False

        if not await _fetch_with_retry(error_id):
            await loading.edit(
                content=f"No matching issues found for error ID: {error_id} "
                "after all attempts."
            )
            self.bot.logger.warning(
                f"No matching issues found for error ID: {error_id} "
                "after all attempts."
            )

    def _create_indexes(self) -> None:
        """Create indexes for the tag collection."""
        self.tag_collection.create_index([("name", 1)], unique=True)

    @staticmethod
    def _get_tag_query(tag_name: str) -> Dict[str, Any]:
        """Generate a MongoDB query for retrieving a tag by name."""
        escaped_tag_name = re.escape(tag_name)
        return {
            "name": {
                "$regex": f"^{escaped_tag_name}$",
                "$options": "i"
            }
        }

    async def run_tag_command(
        self,
        message: discord.Message | commands.Context,
        tag_name: str,
        target_message_id: Optional[int] = None
    ) -> None:
        """Execute a tag command."""
        try:
            query = self._get_tag_query(tag_name)
            tag_document = await self.tag_collection.find_one(query)

            if not tag_document:
                if isinstance(message, commands.Context):
                    await message.send(f"Tag '{tag_name}' not found.")
                return

            tag_content = tag_document.get("content", "No content available")
            target_message_id = (
                message.reference.message_id 
                if getattr(message, 'reference', None) 
                else target_message_id
            )

            try:
                if isinstance(message, commands.Context):
                    await message.message.delete()
                elif hasattr(message, 'delete'):
                    await message.delete()
            except discord.Forbidden:
                pass

            channel = message.channel
            if target_message_id:
                try:
                    target_message = await channel.fetch_message(target_message_id)
                    await target_message.reply(tag_content)
                except discord.NotFound:
                    await channel.send("Target message not found.")
            else:
                await channel.send(tag_content)

        except Exception as e:
            await message.channel.send(
                f"An error occurred while processing the tag: {str(e)}"
            )

    @staticmethod
    async def check_permissions(ctx: commands.Context) -> bool:
        """Check if user has required permissions."""
        support_role = discord.utils.get(ctx.author.roles, name='Support')
        return support_role is not None

    @commands.hybrid_group(
        name='tag',
        description='Tag commands',
        case_insensitive=True
    )
    async def tag_command(
        self,
        ctx: commands.Context,
        tag_name: Optional[str] = None,
        target_message_id: Optional[int] = None
    ) -> None:
        """Tag command group."""
        if tag_name:
            target_message_id = (
                ctx.message.reference.message_id 
                if getattr(ctx.message, 'reference', None) 
                else target_message_id
            )
            await self.run_tag_command(ctx, tag_name, target_message_id)
        elif not ctx.invoked_subcommand:
            await ctx.send("Invalid tag command. Use `!help tag` for more information.")

    @commands.has_any_role('Support')
    @tag_command.command(name='create', description='Create a new tag')
    async def create_tag(
        self,
        ctx: commands.Context,
        tag_name: str,
        *,
        tag_content: str
    ) -> None:
        """Create a new tag."""
        query = self._get_tag_query(tag_name)
        existing_tag = await self.tag_collection.find_one(query)

        if existing_tag:
            await ctx.send(f"A tag with the name '{tag_name}' already exists.")
            return

        tag_data = {
            "author_id": ctx.author.id,
            "name": tag_name,
            "content": tag_content
        }
        await self.tag_collection.update_one(
            query,
            {"$set": tag_data},
            upsert=True
        )
        await ctx.send(f"Tag '{tag_name}' created successfully!")

    async def edit_or_delete_tag(
        self,
        ctx: commands.Context,
        tag_name: str,
        new_tag_content: Optional[str] = None,
        delete: bool = False
    ) -> None:
        """Edit or delete a tag."""
        query = self._get_tag_query(tag_name)
        existing_tag = await self.tag_collection.find_one(query)

        if not existing_tag:
            await ctx.send(f"Tag '{tag_name}' not found.")
            return

        if not await self.check_permissions(ctx):
            await ctx.send("You don't have permission to perform this action.")
            return

        if delete:
            await self.tag_collection.delete_one(query)
            await ctx.send(f"Tag '{tag_name}' deleted successfully!")
        else:
            update_query = {"$set": {"content": new_tag_content}}
            await self.tag_collection.update_one(query, update_query)
            await ctx.send(f"Tag '{tag_name}' edited successfully!")

    @tag_command.command(name='list', description='List all tags')
    async def list_tags(self, ctx: commands.Context) -> None:
        """List all tags with their content."""
        all_tags = await self.tag_collection.find().to_list(length=None)

        if not all_tags:
            await ctx.send("No tags found.")
            return

        pages = []
        for tag in all_tags:
            tag_name = tag["name"]
            tag_content = tag.get("content", "No content available")
            author_id = tag.get("author_id", "Unknown")
            author_mention = (
                f"<@{author_id}>" 
                if author_id != "Unknown" 
                else "Unknown"
            )

            content = (
                f"**Content:**\n > {tag_content}\n"
                f"**Author:**\n >>> {author_mention}"
            )
            embed = discord.Embed(
                title=tag_name,
                description=content,
                color=self._embed_color
            )
            embed.set_author(
                name="Tag List",
                icon_url=str(ctx.guild.icon)
            )
            pages.append(embed)

        paginator = TagListPaginator(bot=self.bot, pages=pages)
        await paginator.start(ctx)

    @tag_command.command(
        name='all',
        description='List all tags in the server'
    )
    async def list_all_tags(self, ctx: commands.Context) -> None:
        """List all tag names."""
        all_tags = await self.tag_collection.find().to_list(length=None)

        if not all_tags:
            await ctx.send("No tags found.")
            return

        tags_list = ", ".join(f"`{tag['name']}`" for tag in all_tags)
        embed = discord.Embed(
            title="Tag List",
            description=tags_list,
            color=self._embed_color
        )
        embed.set_author(
            name="All Tags",
            icon_url=str(ctx.guild.icon)
        )
        await ctx.send(embed=embed)

    @commands.has_any_role('Support')
    @tag_command.command(
        name='edit',
        description='Edit an existing tag'
    )
    async def edit_tag(
        self,
        ctx: commands.Context,
        tag_name: str,
        *,
        new_tag_content: str
    ) -> None:
        """Edit an existing tag."""
        await self.edit_or_delete_tag(ctx, tag_name, new_tag_content)

    @commands.has_any_role('Support')
    @tag_command.command(
        name='delete',
        description='Delete an existing tag'
    )
    async def delete_tag(
        self,
        ctx: commands.Context,
        tag_name: str
    ) -> None:
        """Delete an existing tag."""
        await self.edit_or_delete_tag(ctx, tag_name, delete=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Handle tag commands from messages."""
        if message.author.bot:
            return

        if not message.content.startswith('!'):
            return

        tag_name = message.content[1:].strip()
        if not tag_name:
            return

        try:
            target_message_id = (
                message.reference.message_id 
                if getattr(message, 'reference', None) 
                else None
            )
            await self.run_tag_command(message, tag_name, target_message_id)
        except discord.errors.HTTPException as e:
            if "No matching document" not in str(e):
                await message.channel.send(
                    f"An error occurred while processing the tag: {str(e)}"
                )
        except AttributeError as attr_error:
            await message.channel.send(
                f"An error occurred while processing the tag: {str(attr_error)}"
            )

async def setup(bot: commands.Bot) -> None:
    """Set up the Support cog."""
    await bot.add_cog(Support(bot))
