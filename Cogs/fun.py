import discord
from discord.ext import commands
import json
import aiohttp


class Fun(commands.Cog):
    def __init__(self, bot_instance):
        self.bot = bot_instance
        self.session = aiohttp.ClientSession()
        self.bot.logger.info("Opened aiohttp session in fun cog.")
        with open('config.json', 'r') as config_file:
            self.config = json.load(config_file)

        self.headers = {
            "Dog-API": self.config["DOG_API_KEY"],
            "Cat-API": self.config["CAT_API_KEY"],
            "Accept": 'application/json',
        }

    async def cog_unload(self):
        await self.bot.loop.create_task(self.session.close())
        self.bot.logger.info("Closed aiohttp session in fun cog.")

    @staticmethod
    def is_non_empty_list(data):
        return isinstance(data, list) and bool(data)

    async def _fetch_data(self, url, headers=None, data_type='json'):
        try:
            async with self.session.get(url, headers=headers) as response:
                if response.status == 200:
                    return await getattr(response, data_type)()
                else:
                    return f"Error fetching API.\n* **Status Code:** {response.status}"
        except aiohttp.ClientError as e:
            self.bot.logger.error(f"Request error: {e}")
            return f"Error fetching API. Please try again later."

    async def _process_image(self, ctx, data):
        if isinstance(data, str):
            await ctx.reply(data)
        elif self.is_non_empty_list(data) and isinstance(data[0], dict):
            image_url = data[0].get("url")
            if image_url:
                embed = discord.Embed(color=discord.Color.from_rgb(43, 45, 49))
                embed.set_image(url=image_url)
                await ctx.reply(embed=embed)
            else:
                await ctx.reply("Error extracting image URL from API response.")
        else:
            await ctx.reply("Unexpected response from API.")

    @commands.hybrid_command(name="insult", with_app_command=True, description="Get a random insult")
    async def insult(self, ctx):
        insult_data = await self._fetch_data(self.config['INSULT_API_URL'], data_type='text')
        if isinstance(insult_data, str):
            embed = discord.Embed(
                description=insult_data,
                color=discord.Color.from_rgb(43, 45, 49)
            )
            await ctx.reply(embed=embed)

    @commands.hybrid_command(name="buzzword", with_app_command=True, description="Get a random buzzword")
    async def buzzword(self, ctx):
        buzzword_data = await self._fetch_data(self.config['BUZZWORD_API_URL'])
        if isinstance(buzzword_data, str):
            await ctx.reply(buzzword_data)
        else:
            phrase = buzzword_data.get("phrase")
            if phrase:
                embed = discord.Embed(
                    description=phrase,
                    color=discord.Color.from_rgb(43, 45, 49)
                )
                await ctx.reply(embed=embed)
            else:
                await ctx.reply("Error extracting phrase from Buzzword API response.")

    @commands.hybrid_command(name="dog", with_app_command=True, description="Get a random dog image")
    async def dog(self, ctx):
        data = await self._fetch_data(self.config['DOG_API_URL'], headers=self.headers)
        await self._process_image(ctx, data)

    @commands.hybrid_command(name="cat", with_app_command=True, description="Get a random cat image")
    async def cat(self, ctx):
        data = await self._fetch_data(self.config['CAT_API_URL'], headers=self.headers)
        await self._process_image(ctx, data)

    @commands.hybrid_command(name="meme", with_app_command=True, description="Get a random meme")
    async def meme(self, ctx):
        meme_data = await self._fetch_data(self.config['MEME_API_URL'])

        if isinstance(meme_data, str):
            await ctx.reply(meme_data)
        elif isinstance(meme_data, dict):
            title = meme_data.get('title')
            image_url = meme_data.get('url')

            if title and image_url:
                embed = discord.Embed(
                    title=title,
                    color=discord.Color.from_rgb(43, 45, 49)
                )
                embed.set_image(url=image_url)
                await ctx.reply(embed=embed)
            else:
                await ctx.reply("Error extracting title or image URL from Meme API response.")
        else:
            await ctx.reply("Unexpected response from Meme API.")

    @commands.hybrid_command(name="age", with_app_command=True, description="Get the age of a person")
    async def age(self, ctx, name):
        if name.lower() in ['noah']:
            embed = discord.Embed(
                title='Noah',
                description=f"After consulting your mother, she says that Noah is "
                            f"**69420**.",
                color=discord.Color.from_rgb(43, 45, 49)
            )
            await ctx.reply(embed=embed)
            return

        capitalized_name = name.capitalize()
        age_data = await self._fetch_data(f"{self.config['AGEIFY_URL']}?name={capitalized_name}")
        if isinstance(age_data, str):
            await ctx.reply(age_data)
        else:
            age_data_response = age_data.get("age", "Age not available")
            embed = discord.Embed(
                title=capitalized_name,
                description=f"After consulting your mother, she says that {capitalized_name} is "
                            f"**{age_data_response}**.",
                color=discord.Color.from_rgb(43, 45, 49)
            )
            await ctx.reply(embed=embed)

    @commands.hybrid_command(name="country", with_app_command=True, description="Get information about a country")
    async def country(self, ctx, *, country_name: str):
        if country_name.lower() in ["africa", "african"]:
            embed = discord.Embed(
                description="Africa is not a country, it's a continent..",
                color=discord.Color.from_rgb(43, 45, 49),
            )
            embed.set_image(url="https://media3.giphy.com/media/3o85xnoIXebk3xYx4Q/giphy.gif")
            await ctx.reply(embed=embed)
            return

        country_data = await self._fetch_data(f"{self.config['REST_COUNTRIES_API_URL']}{country_name}")

        if isinstance(country_data, str):
            await ctx.reply(country_data)
        else:
            country_info = country_data[0] if isinstance(country_data, list) and len(country_data) > 0 else None

            if country_info:
                embed = discord.Embed(
                    title=f"Information for {country_info['name']['common']}",
                    color=discord.Color.from_rgb(43, 45, 49),
                )
                embed.add_field(name="Capital", value=country_info.get("capital", ["Not available"])[0], inline=True)
                embed.add_field(name="Region", value=country_info.get("region", "Not available"), inline=True)
                embed.add_field(name="Population", value=country_info.get("population", "Not available"), inline=True)
                await ctx.reply(embed=embed)
            else:
                await ctx.reply("No information available for the specified country.")

    @commands.hybrid_command(name="trump", with_app_command=True, description="Get a random quote from Donald Trump")
    async def trump(self, ctx):
        quote_data = await self._fetch_data(self.config['TRONALD_DUMP_API_URL'], headers=self.headers)
        if isinstance(quote_data, str):
            await ctx.reply(quote_data)
        else:
            quote_response = quote_data.get("value", "No quote available")
            embed = discord.Embed(
                description=quote_response,
                color=discord.Color.from_rgb(43, 45, 49)
            )
            embed.set_author(name="Donald Trump")
            await ctx.reply(embed=embed)


async def setup(bot_instance):
    await bot_instance.add_cog(Fun(bot_instance))