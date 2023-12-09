import discord
from discord.ext import commands


class Utility(commands.Cog):
    def __init__(self, bot_instance):
        self.bot = bot_instance

    @commands.hybrid_command(name="ping", with_app_command=True, description="Get the bots latency")
    async def ping(self, ctx):
        latency = self.bot.latency * 1000
        embed = discord.Embed(
            description=f'Pong: {latency:.0f}ms',
            color=discord.Color.from_rgb(43, 45, 49)
        )
        await ctx.reply(embed=embed)


async def setup(bot_instance):
    await bot_instance.add_cog(Utility(bot_instance))
