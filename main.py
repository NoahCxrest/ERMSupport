import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
import logging
import asyncio
import aiohttp
import time

load_dotenv()


class Config:
    TOKEN = os.getenv("TOKEN")


def load_config():
    load_dotenv()
    return Config


BOT_PREFIX = '?'
LOG_CHANNEL = 1173467080704655515


class Bot(commands.AutoShardedBot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.logger = self.setup_logger()
        self.session = None
        self.is_ready = asyncio.Event()

    @staticmethod
    def setup_logger():
        logger = logging.getLogger(__name__)
        handler = logging.StreamHandler()
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        return logger

    async def on_ready(self):
        start_time = time.time()
        self.session = aiohttp.ClientSession()

        self.logger.info(f"Logged in as {self.user.name}#{self.user.discriminator}")
        elapsed_time = (time.time() - start_time) * 1000
        self.logger.info(f"Bot is ready. Took {elapsed_time}ms")

        log_channel = self.get_channel(LOG_CHANNEL)
        if log_channel is not None:
            await log_channel.send(f"Bot is ready. Took {elapsed_time}ms")
        else:
            self.logger.warning(f"Log channel not found: {LOG_CHANNEL}")

        await self.load_extensions()
        self.is_ready.set()

    async def load_extensions(self):
        self.logger.debug("Loading extensions...")
        extensions = [filename[:-3] for filename in os.listdir(os.path.join('Cogs')) if filename.endswith('.py')]

        self.logger.info(f"Found extensions: {extensions}")

        tasks = [self.load_extension(f'Cogs.{extension}') for extension in extensions]

        try:
            tasks.append(self.load_extension('jishaku'))
        except commands.ExtensionError as e:
            self.logger.error(f"{e}")
            raise e

        await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()

        await super().close()


intents = discord.Intents.default()
intents.message_content = True
bot = Bot(command_prefix=BOT_PREFIX, intents=intents)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        bot.logger.warning(f"Command not found: {ctx.message.content}")
        return

    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.reply("You're missing some arguments.")
        return

    if hasattr(ctx.command, 'on_error'):
        return

    error_message = f"Something went wrong. 👇\n* {str(error)}"
    await ctx.reply(content=error_message)

    error_channel = bot.get_channel(LOG_CHANNEL)
    if error_channel is not None:
        await error_channel.send(content=error_message)
    else:
        bot.logger.warning(f"Error channel not found: {LOG_CHANNEL}")


async def main():
    try:
        await bot.start(Config.TOKEN)
    except asyncio.CancelledError:
        bot.logger.error("The operation was cancelled.")
        raise

if __name__ == "__main__":
    asyncio.run(main())
