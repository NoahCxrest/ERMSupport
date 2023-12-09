import discord
from discord.ext import commands
from discord import ForumTag
import json
import asyncio
from datetime import datetime, timezone
import time
from discord.utils import format_dt
import aiohttp


class Support(commands.Cog):
    def __init__(self, bot_instance):
        self.bot = bot_instance
        self.config = None
        self.headers = None
        self.closed_threads_file = "closed_threads.json"
        self.session = aiohttp.ClientSession()
        self.bot.logger.info("Opened aiohttp session in support cog.")
        with open('config.json', 'r') as config_file:
            self.config = json.load(config_file)

        try:
            with open(self.closed_threads_file, "r") as file:
                self.closed_threads = set(json.load(file))
        except FileNotFoundError:
            self.closed_threads = set()

        self.headers = {
            "Authorization": f"Bearer {self.config['SENTRY_API_KEY']}",
        }

    async def cog_unload(self):
        await self.bot.loop.create_task(self.session.close())
        self.bot.logger.info("Closed aiohttp session in support cog.")

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def fetch_issues(self, error_id: str):
        url = (f"{self.config['SENTRY_API_URL']}/projects/{self.config['SENTRY_ORGANIZATION_SLUG']}/"
               f"{self.config['PROJECT_SLUG']}/issues/")
        query_params = {"query": f"error_id:{error_id}"}

        try:
            async with self.session.get(url, headers=self.headers, params=query_params) as response:
                if response.status == 200:
                    json_data = await response.json()
                    self.bot.logger.info(f"Successfully fetched issues: {json_data}")
                    return json_data
                else:
                    self.bot.logger.warning(f"Failed to fetch issues. Status code: {response.status}")
                    self.bot.logger.warning(await response.text())
                    return None
        except Exception as e:
            self.bot.logger.error(f"Error fetching issues: {e}")
            return None

    def process_response(self, issues):
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

        self.bot.logger.info(f"Processed issue data: {issue_data}")

        return title, value, handled, last_seen_formatted

    async def update_ui(self, loading, title, value, handled, last_seen, start_time):
        self.bot.logger.info("Updating UI with fetched data.")
        embed = discord.Embed(title=f"Sentry Issue: {title}", color=discord.Color.from_rgb(43, 45, 49))
        embed.add_field(name="Value", value=value, inline=False)
        embed.add_field(name="Unhandled", value=handled, inline=False)
        embed.add_field(name="Last Seen", value=last_seen, inline=False)

        end_time = time.time()
        elapsed_time = (end_time - start_time) * 1000
        time_content = f"Fetched details at {'normal' if elapsed_time < 1900 else 'slow'} speed. ``{elapsed_time}ms``."
        await loading.edit(content=time_content, embed=embed)
        self.bot.logger.info("UI updated successfully.")

    @commands.command(name="sentry", description="Get a Sentry issue by error ID")
    @commands.has_any_role('Support')
    async def sentry(self, ctx, error_id: str):
        loading = await ctx.reply(content=f"Fetching...")
        self.bot.logger.info(f"Fetching issues for error ID: {error_id}")

        async def fetch_issues_with_retry(error_id_param: str, loading_message, fetch_start_time,
                                          max_attempts: int = 2, retry_interval: int = 5):
            for attempt in range(1, max_attempts + 1):
                issues = await self.fetch_issues(error_id_param)
                self.bot.logger.info(f"Issues fetched on attempt {attempt}: {issues}")

                if issues is not None:
                    issue_data = self.process_response(issues)
                    if issue_data is not None:
                        self.bot.logger.info("Issue found. Updating UI.")
                        await self.update_ui(loading_message, *issue_data, start_time=fetch_start_time)
                        return True

                next_attempt_time = int(time.time()) + retry_interval
                await loading_message.edit(
                    content=f"No matching issues found for error ID: {error_id_param}... **Trying again "
                            f"<t:{next_attempt_time}:R>**.")
                await asyncio.sleep(retry_interval)

            return False

        start_time = time.time()
        if not await fetch_issues_with_retry(error_id, loading, start_time):
            await loading.edit(content=f"No matching issues found for error ID: {error_id} after all attempts.")
            self.bot.logger.warning(f"No matching issues found for error ID: {error_id} after all attempts.")

        self.bot.logger.info("Finished fetching and processing issues.")

    @commands.hybrid_command(name='close', aliases=['c'])
    async def close(self, ctx):
        if not await self._validate_context(ctx):
            return

        thread = await ctx.guild.fetch_channel(ctx.channel.id)
        guild = ctx.guild

        if not guild:
            await ctx.reply("Guild information not available.")
            return

        owner = await guild.fetch_member(thread.owner_id)

        if not owner:
            await ctx.reply("Owner not found.")
            return

        tags = self._get_tags(ctx)
        self._append_tags(tags, [1131688319617605835], ["Thread Resolved"])

        tasks = [self._try_close_thread(ctx, tags), self._send_close_message(ctx, owner)]
        await asyncio.gather(*tasks)

        self._save_closed_threads()

    async def _validate_context(self, ctx):
        try:
            if ctx.channel.guild.id != 1173467080075526305 or ctx.channel.parent_id != 1177026577989644358:
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
        tags_to_remove = ['Awaiting Support', 'Developers Required', 'Thread Paused']
        return [tag for tag in ctx.channel.applied_tags if tag.name not in tags_to_remove]

    @staticmethod
    def _append_tags(tags, tag_ids, tag_names):
        for tag_id, tag_name in zip(tag_ids, tag_names):
            tag = ForumTag(name=tag_name)
            tag.id = tag_id
            if tag not in tags:
                tags.append(tag)

    @staticmethod
    async def _try_close_thread(ctx, tags):
        try:
            await ctx.channel.edit(archived=True, applied_tags=tags, reason='Closing thread and marking as resolved')
        except discord.HTTPException:
            await ctx.reply("An error occurred while closing the thread.")
            return False

        return True

    async def _send_close_message(self, ctx, thread_owner):
        embed = discord.Embed(
            title=f'<:success:1178163443170291773> Thread Closed',
            description=f'**{ctx.author.display_name}** has closed this thread.',
            color=0x65d07d,
        )
        embed.set_author(name=ctx.author.display_name, icon_url=str(ctx.author.avatar))

        await ctx.reply(embed=embed, view=Support.ButtonView(self.bot, thread_owner))

        self.closed_threads.add(ctx.channel.id)

    @staticmethod
    def _write_to_file(filename, data):
        with open(filename, 'w') as f:
            json.dump(data, f)

    def _save_closed_threads(self):
        loop = asyncio.get_event_loop()
        loop.call_soon_threadsafe(self._write_to_file, self.closed_threads_file, list(self.closed_threads))

    class Questionnaire(discord.ui.Modal, title='✨ Review a Staff Member'):
        name = discord.ui.TextInput(label='Name')
        answer = discord.ui.TextInput(label='Answer', style=discord.TextStyle.paragraph)
        rating = discord.ui.TextInput(label='Rating')

        async def on_submit(self, interaction: discord.Interaction):
            rating = int(self.rating.value)
            rating = max(1, min(rating, 5))
            stars = '⭐' * rating

            embed = discord.Embed(
                title=f'{interaction.user.display_name} responded with:',
                color=discord.Color.from_rgb(43, 45, 49),
            )
            embed.add_field(name='Note', value=self.answer.value, inline=False)
            embed.add_field(name='Rating', value=stars, inline=False)
            channel_id = 1037896352484565053  # Channel ID for #reviews, change if needed.
            review_channel = interaction.guild.get_channel(channel_id)

            if review_channel:
                await review_channel.send(content=f"<#{interaction.channel.id}>", embed=embed)
            else:
                await interaction.response.send_message("Error: Review channel not found.",
                                                        ephemeral=True, delete_after=3)

            await interaction.response.send_message("Your review has been submitted.",
                                                    ephemeral=True, delete_after=3)

    class ButtonView(discord.ui.View):
        def __init__(self, bot_instance, thread_author, *, timeout=None):
            super().__init__(timeout=timeout)
            self.bot = bot_instance
            self.thread_author = thread_author

        @discord.ui.button(label="✨ Submit a Review", style=discord.ButtonStyle.primary)
        async def button_callback(self, interaction: discord.Interaction, _: discord.ui.Button):
            if interaction.user != self.thread_author:
                await interaction.response.send_message("Only the thread creator can submit a review. Big L.",
                                                        ephemeral=True, delete_after=3)
                print(interaction.user, self.thread_author)
                self.bot.logger.warning(f"{interaction.user.name} tried to submit a review.")
            else:
                await interaction.response.send_modal(Support.Questionnaire())

    class DeleteButton(discord.ui.View):
        allowed_role_id = 988055417907200010

        def __init__(self, bot_instance, message_id, channel_id, response_message, jump_url, *, timeout=None):
            super().__init__(timeout=timeout)
            self.bot = bot_instance
            self.message_id = message_id
            self.channel_id = channel_id
            self.response_message = response_message
            self.jump_url = jump_url

        @discord.ui.button(label="Quick Delete", style=discord.ButtonStyle.red)
        async def quick_delete_callback(self, interaction: discord.Interaction, _: discord.ui.Button):
            try:
                channel = await self.bot.fetch_channel(int(self.channel_id))
                self.bot.logger.info(f"Attempting to delete message with ID {self.message_id}")

                member, message = await asyncio.gather(
                    interaction.guild.fetch_member(interaction.user.id),
                    channel.fetch_message(self.message_id),
                )

                if self.allowed_role_id in [role.id for role in member.roles]:
                    deletion_tasks = [
                        self.response_message.delete(),
                        message.delete(),
                        interaction.message.delete()
                    ]
                    await asyncio.gather(*deletion_tasks)

                    self.stop()
                else:
                    await interaction.response.send_message("You do not have the required role to delete this message.",
                                                            ephemeral=True, delete_after=3)

            except discord.NotFound as e:
                self.bot.logger.error(f"Message with ID {self.message_id} not found. Error: {e}")
            except discord.Forbidden as e:
                self.bot.logger.error(f"Bot does not have permission to delete messages in channel {self.channel_id}. "
                                      f"Error: {e}")
            except Exception as e:
                self.bot.logger.error(f"An error occurred: {e}")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if str(payload.emoji) != '⚠️':
            return

        user, channel, original_message = await self._fetch_user_channel_message(payload)
        if not user or not channel or not original_message:
            return

        if user.bot:
            return

        report_channel = self.bot.get_channel(988056281900257300)
        if report_channel is None:
            return

        jump_url = original_message.jump_url
        embed = self._create_report_embed(user)
        response_message = await report_channel.send(
            content=f"<@&988055417907200010>\n[Jump to Message]({jump_url})",
            embed=embed,
        )

        delete_button_view = Support.DeleteButton(
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

        await delete_button_view.wait()
        await self._delete_messages(original_message, response_message, quick_delete_message)

    async def _fetch_user_channel_message(self, payload):
        try:
            user = await self.bot.fetch_user(payload.user_id)
            channel = await self.bot.fetch_channel(payload.channel_id)
            original_message = await channel.fetch_message(payload.message_id)
            return user, channel, original_message
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


async def setup(bot_instance):
    await bot_instance.add_cog(Support(bot_instance))
