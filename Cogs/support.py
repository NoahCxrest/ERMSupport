from discord.ext import commands
from discord import ForumTag
import json
import asyncio
from datetime import datetime, timezone
from discord.utils import format_dt
from motor.motor_asyncio import AsyncIOMotorClient
import re
from menus import TagListPaginator, ButtonView, DeleteButton
import discord


class Support(commands.Cog):
    def __init__(self, bot_instance):
        self.bot = bot_instance
        self.config = self.load_config()
        self.session = self.bot.session
        self.client = AsyncIOMotorClient(self.config['MONGO_URI'])
        self.database = self.client["Cronus"]
        self.collection = self.database["threads"]
        self.tag_collection = self.database["tags"]
        self.headers = {"Authorization": f"Bearer {self.config['SENTRY_API_KEY']}"}
        self.closed_threads = set()
        self.last_report_times = {}
        self.last_reaction_time = datetime.min
        self.create_indexes()

    @staticmethod
    def load_config():
        """Load the bots' configuration."""
        with open('./config.json', 'r') as config_file:
            return json.load(config_file)

    async def _fetch_issues(self, error_id: str):
        """Fetch issues from the Sentry API."""
        url = f"{self.config['SENTRY_API_URL']}/projects/{self.config['SENTRY_ORGANIZATION_SLUG']}/" \
              f"{self.config['PROJECT_SLUG']}/issues/"
        query_params = {"query": f"error_id:{error_id}"}

        try:
            async with self.session.get(url, headers=self.headers, params=query_params) as response:
                if response.status == 200:
                    json_data = await response.json()
                    return json_data
                else:
                    self.bot.logger.warning(await response.text())
                    return None
        except Exception as e:
            self.bot.logger.error(f"Error fetching issues: {e}")
            return None

    def _process_response(self, issues):
        """Process the response from the Sentry API."""
        if not issues:
            self.bot.logger.warning("No issues found in response.")
            return None

        issue_data = issues[0]
        title = issue_data.get('title', 'Title not available')
        value = issue_data.get('metadata', {}).get('value', 'Value not available')
        handled = issue_data.get('isUnhandled', 'Handled information not available')
        last_seen = issue_data.get('lastSeen', 'Last seen not available')

        last_seen_dt = datetime.fromisoformat(last_seen.replace('Z', '+00:00')).replace(tzinfo=timezone.utc)
        last_seen_formatted = format_dt(last_seen_dt, style='R')

        return title, value, handled, last_seen_formatted

    @staticmethod
    async def _update_ui(loading, title, value, handled, last_seen):
        """Update the UI with the issue data."""
        embed = discord.Embed(title=f"Sentry Issue: {title}", color=discord.Color.from_rgb(43, 45, 49))
        embed.add_field(name="Value", value=value, inline=False)
        embed.add_field(name="Unhandled", value=handled, inline=False)
        embed.add_field(name="Last Seen", value=last_seen, inline=False)
        await loading.edit(content=None, embed=embed)

    @commands.hybrid_command(name="sentry", description="Get a Sentry issue by error ID")
    @commands.has_any_role('Support')
    async def sentry(self, ctx, error_id: str):
        """Get a Sentry issue by error ID."""
        loading = await ctx.reply(content=f"Fetching...")

        async def _fetch_issues_with_retry(
                error_id_param: str,
                max_attempts: int = 4,
                initial_retry_interval: int = 2,
        ):
            for attempt in range(1, max_attempts + 1):
                issues = await self._fetch_issues(error_id_param)

                if issues is not None:
                    issue_data = self._process_response(issues)
                    if issue_data is not None:
                        await self._update_ui(loading, *issue_data)
                        return True

                retry_interval = initial_retry_interval * 1.3 ** (attempt - 1)
                await loading.edit(
                    content=f"No matching issues found for error ID: {error_id_param}... **Retrying in "
                            f"{retry_interval} seconds**."
                )
                await asyncio.sleep(retry_interval)

            return False

        if not await _fetch_issues_with_retry(error_id):
            await loading.edit(content=f"No matching issues found for error ID: {error_id} after all attempts.")
            self.bot.logger.warning(f"No matching issues found for error ID: {error_id} after all attempts.")

    @commands.hybrid_command(name='close', aliases=['c'], with_app_command=True, description='Close a support thread')
    async def close(self, ctx):
        if not await self._validate_context(ctx):
            return

        thread_data = {"thread_id": ctx.channel.id}
        await ctx.defer()

        try:
            thread = await ctx.guild.fetch_channel(ctx.channel.id)
            guild = ctx.guild
            if not guild:
                raise ValueError("Guild information not available.")

            owner = await guild.fetch_member(thread.owner_id)
            if not owner:
                raise ValueError("Owner not found.")

            tags = self._get_tags(ctx)
            self._append_tags(tags, [1131688319617605835], ["Thread Resolved"])

            success = await self._try_close_thread(ctx, tags)
            if success:
                await self._send_close_message(ctx, ctx.author)

            await self._save_closed_threads(thread_data)

        except ValueError as e:
            await self._reply_error(ctx, str(e))

    @staticmethod
    async def _reply_error(ctx, message):
        embed = discord.Embed(title="Error", description=message, color=0xFF0000)
        await ctx.reply(embed=embed)

    async def _validate_context(self, ctx):
        """Validate the context of the command."""
        try:
            if ctx.channel.guild.id != 987798554972143728 and ctx.channel.parent_id != 1131680482170507335:
                return False
        except AttributeError:
            await ctx.reply("This command can only be used in support threads.")
            return False

        if not isinstance(ctx.channel, discord.Thread):
            await ctx.reply("This command can only be used in threads.")
            return False

        if ctx.channel.id in self.closed_threads:
            await ctx.reply("This thread has already been closed.")
            return False

        return True

    @staticmethod
    def _get_tags(ctx):
        """Get the tags to apply to the thread."""
        tags_to_remove = ['Awaiting Support', 'Developers Required', 'Thread Paused']
        return [tag for tag in ctx.channel.applied_tags if tag.name not in tags_to_remove]

    @staticmethod
    def _append_tags(tags, tag_ids, tag_names):
        """Append tags to the list of tags."""
        for tag_id, tag_name in zip(tag_ids, tag_names):
            tag = ForumTag(name=tag_name)
            tag.id = tag_id
            if tag not in tags:
                tags.append(tag)

    @staticmethod
    async def _try_close_thread(ctx, tags):
        """Attempt to close the thread."""
        try:
            await asyncio.wait_for(
                ctx.channel.edit(archived=True, applied_tags=tags, reason='Closing thread and marking as resolved'),
                timeout=10
            )
        except asyncio.TimeoutError:
            await ctx.reply("The operation timed out.")
            return False
        except discord.HTTPException:
            await ctx.reply("An error occurred while closing the thread.")
            return False
        return True

    async def _send_close_message(self, ctx, thread_owner):
        """Send a message to the thread owner indicating that the thread has been closed."""
        embed = discord.Embed(
            title='<:success:1178163443170291773> Thread Closed',
            description=f'**{ctx.author.display_name}** has closed this thread.',
            color=0x65d07d,
        )
        embed.set_author(name=ctx.author.display_name, icon_url=str(ctx.author.avatar))

        # Use ButtonView directly
        await ctx.send(embed=embed, view=ButtonView(self.bot, thread_owner))

        self.closed_threads.add(ctx.channel.id)

    async def _save_closed_threads(self, thread_data):
        """Save the thread ID to the database."""
        thread_id = thread_data["thread_id"]
        self.closed_threads.add(thread_id)

        await self._write_to_mongodb(thread_id)

    async def _write_to_mongodb(self, thread_id):
        """Write the thread ID to MongoDB."""
        await self.collection.insert_one({"thread_id": thread_id})

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        now = datetime.now()
        cooldown_time = 120
        report_cooldown_time = 20 * 60
        report_channel_id = 988056281900257300

        if (now - self.last_reaction_time).total_seconds() < cooldown_time:
            return

        self.last_reaction_time = now

        if str(payload.emoji) != '⚠️':
            self.bot.logger.warning(f"Unexpected emoji: {payload.emoji}")
            return

        if payload.message_id in self.last_report_times:
            time_since_last_report = now - self.last_report_times[payload.message_id]
            if time_since_last_report.total_seconds() < report_cooldown_time:
                return

        user, channel, original_message = await self._fetch_user_channel_message(payload)
        if not user or not channel or not original_message or user.bot:
            return

        report_channel = self.bot.get_channel(report_channel_id)
        if report_channel is None:
            return

        jump_url = original_message.jump_url
        embed = self._create_report_embed(user)
        response_message = await report_channel.send(
            content=f"<@&PLACEHOLDER>\n[Jump to Message]({jump_url})",
            embed=embed,
        )

        delete_button_view = DeleteButton(
            bot_instance=self.bot,
            message_id=payload.message_id,
            channel_id=payload.channel_id,
            response_message=response_message,
            jump_url=jump_url
        )

        quick_delete_message = await original_message.reply(
            content=f"[Jump to Message]({jump_url})",
            view=delete_button_view
        )

        self.last_report_times[payload.message_id] = now
        await delete_button_view.wait()
        await self._delete_messages(original_message, response_message, quick_delete_message)

    async def _fetch_user_channel_message(self, payload):
        try:
            user = await self.bot.fetch_user(payload.user_id)
            channel = await self.bot.fetch_channel(payload.channel_id)
            message = await channel.fetch_message(payload.message_id)
            return user, channel, message
        except discord.errors.NotFound:
            return None, None, None

    @staticmethod
    def _create_report_embed(user):
        return discord.Embed(
            title="New Report",
            description=f"The user {user.mention} has been reported for sending a message that violates our rules.",
            color=discord.Color.from_rgb(43, 45, 49)
        )

    @staticmethod
    async def _delete_messages(original_message, response_message, quick_delete_message):
        for message in [original_message, response_message, quick_delete_message]:
            try:
                await message.delete()
            except discord.errors.NotFound:
                continue

    def create_indexes(self):
        """Create indexes for the tag collection."""
        self.tag_collection.create_index([("name", 1)], unique=True)

    @staticmethod
    def get_tag_query(tag_name: str):
        """Generate a MongoDB query for retrieving a tag by name."""
        escaped_tag_name = re.escape(tag_name)
        return {"name": {"$regex": f"^{escaped_tag_name}$", "$options": "i"}}

    async def run_tag_command(self, message, tag_name: str, target_message_id: int = None):
        try:
            query = self.get_tag_query(tag_name)
            tag_document = await self.tag_collection.find_one(query)

            if not tag_document:
                if isinstance(message, commands.Context):
                    return await message.send(f"Tag '{tag_name}' not found.")

                return

            tag_content = tag_document.get("content", "No content available")

            target_message_id = (
                message.reference.message_id if getattr(message, 'reference', None) else target_message_id
            )

            try:
                if isinstance(message, commands.Context):
                    await message.message.delete()
                elif hasattr(message, 'delete'):
                    await message.delete()
            except discord.Forbidden:
                pass

            if target_message_id:
                try:
                    target_message = await message.channel.fetch_message(target_message_id)
                    await target_message.reply(tag_content)
                except discord.NotFound:
                    await message.channel.send("Target message not found.")
            else:
                await message.channel.send(tag_content)

        except Exception as e:
            await message.channel.send(f"An error occurred while processing the tag: {str(e)}")

    @staticmethod
    async def check_permissions(ctx):
        support_role = discord.utils.get(ctx.author.roles, name='Support')
        return support_role is not None

    @commands.hybrid_group(name='tag', description='Tag commands', case_insensitive=True)
    async def tag_command(self, ctx, tag_name: str = None):
        if tag_name:
            await self.run_tag_command(ctx, tag_name)
        elif not ctx.invoked_subcommand:
            await ctx.send("Invalid tag command. Use `!help tag` for more information.")

    @commands.has_any_role('Support')
    @tag_command.command(name='create', description='Create a new tag')
    async def create_tag(self, ctx, tag_name: str, *, tag_content: str):
        query = self.get_tag_query(tag_name)
        existing_tag = await self.tag_collection.find_one(query)

        if existing_tag:
            return await ctx.send(f"A tag with the name '{tag_name}' already exists.")

        tag_data = {"author_id": ctx.author.id, "name": tag_name, "content": tag_content}
        await self.tag_collection.update_one(query, {"$set": tag_data}, upsert=True)
        await ctx.send(f"Tag '{tag_name}' created successfully!")

    async def edit_or_delete_tag(self, ctx, tag_name: str, new_tag_content: str = None, delete: bool = False):
        query = self.get_tag_query(tag_name)
        existing_tag = await self.tag_collection.find_one(query)

        if existing_tag:
            if await self.check_permissions(ctx):
                if delete:
                    await self.tag_collection.delete_one(query)
                    await ctx.send(f"Tag '{tag_name}' deleted successfully!")
                else:
                    update_query = {"$set": {"content": new_tag_content}}
                    await self.tag_collection.update_one(query, update_query)
                    await ctx.send(f"Tag '{tag_name}' edited successfully!")
            else:
                await ctx.send("You don't have permission to perform this action.")
        else:
            await ctx.send(f"Tag '{tag_name}' not found.")

    @tag_command.command(name='list', description='List all tags')
    async def list_tags(self, ctx):
        all_tags = await self.tag_collection.find().to_list(length=None)

        if not all_tags:
            return await ctx.send("No tags found.")

        pages = []
        for tag in all_tags:
            tag_name = tag["name"]
            tag_content = tag.get("content", "No content available")
            author_id = tag.get("author_id", "Unknown")
            author_mention = f"<@{author_id}>" if author_id != "Unknown" else "Unknown"

            content = f"**Content:**\n > {tag_content}\n**Author:**\n >>> {author_mention}"
            embed = discord.Embed(title=tag_name, description=content, color=discord.Color.from_rgb(43, 45, 49))
            embed.set_author(name="Tag List", icon_url=str(ctx.guild.icon))
            pages.append(embed)

        # Create a paginator view
        paginator = TagListPaginator(bot=self.bot, pages=pages)
        await paginator.start(ctx)

    @tag_command.command(name='all', description='List all tags in the server')
    async def list_all_tags(self, ctx):
        all_tags = await self.tag_collection.find().to_list(length=None)

        if not all_tags:
            return await ctx.send("No tags found.")

        tags_list = ", ".join(f"`{tag['name']}`" for tag in all_tags)
        embed = discord.Embed(
            title="Tag List",
            description=tags_list,
            color=discord.Color.from_rgb(43, 45, 49)
        )
        embed.set_author(name="All Tags", icon_url=str(ctx.guild.icon))
        await ctx.send(embed=embed)

    @commands.has_any_role('Support')
    @tag_command.command(name='edit', description='Edit an existing tag')
    async def edit_tag(self, ctx, tag_name: str, *, new_tag_content: str):
        await self.edit_or_delete_tag(ctx, tag_name, new_tag_content)

    @commands.has_any_role('Support')
    @tag_command.command(name='delete', description='Delete an existing tag')
    async def delete_tag(self, ctx, tag_name: str):
        await self.edit_or_delete_tag(ctx, tag_name, delete=True)

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return

        if message.content.startswith('!'):
            tag_name = message.content[1:].strip()

            if tag_name:
                try:
                    if ' ' not in tag_name:
                        await self.run_tag_command(message, tag_name)
                    else:
                        await self.run_tag_command(message, f"tag {tag_name}")
                except discord.errors.HTTPException as e:
                    if "No matching document" in str(e):
                        pass
                    else:
                        await message.channel.send(f"An error occurred while processing the tag: {str(e)}")
                except AttributeError as attr_error:
                    await message.channel.send(f"An error occurred while processing the tag: {str(attr_error)}")


async def setup(bot_instance):
    await bot_instance.add_cog(Support(bot_instance))
