import discord
from discord import app_commands
import sqlite3
from typing import Optional
from datetime import datetime
import asyncio
import os
import mercadopago


class Client(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.setup_database()
        self.voice_check_task = None

    def setup_database(self):
        self.conn = sqlite3.connect('economy.db')
        self.cursor = self.conn.cursor()

        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS economy (
                user_id INTEGER PRIMARY KEY,
                balance INTEGER DEFAULT 0,
                last_daily TIMESTAMP
            )
        ''')

        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                content TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                message_count INTEGER DEFAULT 0
            )
        ''')

        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS excepted_users (
                user_id INTEGER PRIMARY KEY
            )
        ''')
        self.conn.commit()

    async def setup_hook(self):
        guild = discord.Object(id=1326926349448904769)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        self.voice_check_task = self.loop.create_task(self.check_voice_channels())

    async def check_voice_channels(self):
        while True:
            try:
                for guild in self.guilds:
                    for voice_channel in guild.voice_channels:
                        for member in voice_channel.members:
                            if not member.bot and not member.voice.afk and not member.voice.self_deaf:
                                self.cursor.execute('SELECT 1 FROM excepted_users WHERE user_id = ?', (member.id,))
                                if not self.cursor.fetchone():
                                    ensure_user_exists(member.id)
                                    self.cursor.execute('''
                                        UPDATE economy 
                                        SET balance = balance + 600
                                        WHERE user_id = ?
                                    ''', (member.id,))
                                    self.conn.commit()

            except Exception as e:
                print(f"Erro ao verificar canais de voz: {e}")

            await asyncio.sleep(60)


client = Client()


def is_user_excepted(user_id: int) -> bool:
    client.cursor.execute('SELECT 1 FROM excepted_users WHERE user_id = ?', (user_id,))
    return bool(client.cursor.fetchone())


def ensure_user_exists(user_id: int):
    client.cursor.execute('''
        INSERT OR IGNORE INTO economy (user_id, balance)
        VALUES (?, 0)
    ''', (user_id,))
    client.conn.commit()


def handle_message_reward(user_id: int):
    client.cursor.execute('SELECT 1 FROM excepted_users WHERE user_id = ?', (user_id,))
    if client.cursor.fetchone():
        return False

    client.cursor.execute('''
        SELECT COUNT(*) FROM messages 
        WHERE user_id = ?
    ''', (user_id,))
    message_count = client.cursor.fetchone()[0]

    if message_count % 10 == 0 and message_count > 0:
        client.cursor.execute('''
            UPDATE economy 
            SET balance = balance + 300
            WHERE user_id = ?
        ''', (user_id,))
        client.conn.commit()
        return True
    return False


@client.tree.command()
async def saldo(
        interaction: discord.Interaction,
        usuario: Optional[discord.Member] = None
):
    target_user = usuario or interaction.user

    if not (interaction.user == target_user or interaction.user.guild_permissions.administrator):
        await interaction.response.send_message(
            "❌ Você não tem permissão para consultar o saldo de outros usuários.",
            ephemeral=True
        )
        return

    ensure_user_exists(target_user.id)

    client.cursor.execute('SELECT balance FROM economy WHERE user_id = ?', (target_user.id,))
    balance = client.cursor.fetchone()[0] / 100

    embed = discord.Embed(
        title="💰 Consulta de Saldo",
        description=f"O saldo foi consultado por {interaction.user.mention}",
        color=discord.Color.gold()
    )

    if target_user == interaction.user:
        embed.add_field(
            name="Seu saldo atual:",
            value=f"**{balance:,.2f} Deadcoins**",
            inline=False
        )
    else:
        embed.add_field(
            name=f"Saldo de {target_user.display_name}:",
            value=f"**{balance:,.2f} Deadcoins**",
            inline=False
        )

    embed.set_thumbnail(url=target_user.display_avatar.url)
    embed.set_footer(
        text="Sistema de economia",
        icon_url=client.user.display_avatar.url
    )

    await interaction.response.send_message(embed=embed, ephemeral=True)


@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    ensure_user_exists(message.author.id)

    client.cursor.execute('''
        INSERT INTO messages (user_id, content)
        VALUES (?, ?)
    ''', (message.author.id, message.content))
    client.conn.commit()

    handle_message_reward(message.author.id)


@client.tree.command()
async def addsaldo(
        interaction: discord.Interaction,
        usuario: discord.Member,
        quantidade: float
):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "❌ Você não tem permissão para adicionar saldo.",
            ephemeral=True
        )
        return

    ensure_user_exists(usuario.id)
    quantidade_cents = int(quantidade * 100)  # Convert to cents for storage

    client.cursor.execute('''
        UPDATE economy 
        SET balance = balance + ?
        WHERE user_id = ?
    ''', (quantidade_cents, usuario.id))
    client.conn.commit()

    embed = discord.Embed(
        title="💰 Saldo Adicionado",
        description=f"Saldo adicionado por {interaction.user.mention}",
        color=discord.Color.green()
    )

    embed.add_field(
        name="Usuário:",
        value=f"{usuario.mention}",
        inline=False
    )

    embed.add_field(
        name="Quantidade adicionada:",
        value=f"**{quantidade:,.2f} Deadcoins**",
        inline=False
    )

    embed.set_thumbnail(url=usuario.display_avatar.url)
    embed.set_footer(
        text="Sistema de economia",
        icon_url=client.user.display_avatar.url
    )

    await interaction.response.send_message(embed=embed)


@client.tree.command()
async def removesaldo(
        interaction: discord.Interaction,
        usuario: discord.Member,
        quantidade: float
):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "❌ Você não tem permissão para remover saldo.",
            ephemeral=True
        )
        return

    ensure_user_exists(usuario.id)
    quantidade_cents = int(quantidade * 100)  # Convert to cents for storage

    # Check if user has enough balance
    client.cursor.execute('SELECT balance FROM economy WHERE user_id = ?', (usuario.id,))
    current_balance = client.cursor.fetchone()[0]

    if current_balance < quantidade_cents:
        await interaction.response.send_message(
            f"❌ {usuario.mention} não possui saldo suficiente para esta operação.",
            ephemeral=True
        )
        return

    client.cursor.execute('''
        UPDATE economy 
        SET balance = balance - ?
        WHERE user_id = ?
    ''', (quantidade_cents, usuario.id))
    client.conn.commit()

    embed = discord.Embed(
        title="💰 Saldo Removido",
        description=f"Saldo removido por {interaction.user.mention}",
        color=discord.Color.red()
    )

    embed.add_field(
        name="Usuário:",
        value=f"{usuario.mention}",
        inline=False
    )

    embed.add_field(
        name="Quantidade removida:",
        value=f"**{quantidade:,.2f} Deadcoins**",
        inline=False
    )

    embed.set_thumbnail(url=usuario.display_avatar.url)
    embed.set_footer(
        text="Sistema de economia",
        icon_url=client.user.display_avatar.url
    )

    await interaction.response.send_message(embed=embed)


@client.tree.command()
async def resetsaldo(
        interaction: discord.Interaction,
        usuario: discord.Member
):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "❌ Você não tem permissão para resetar saldo.",
            ephemeral=True
        )
        return

    client.cursor.execute('SELECT balance FROM economy WHERE user_id = ?', (usuario.id,))
    old_balance = client.cursor.fetchone()[0] / 100  # Convert to reais

    client.cursor.execute('''
        UPDATE economy 
        SET balance = 0
        WHERE user_id = ?
    ''', (usuario.id,))
    client.conn.commit()

    embed = discord.Embed(
        title="🔄 Saldo Resetado",
        description=f"Saldo resetado por {interaction.user.mention}",
        color=discord.Color.red()
    )

    embed.add_field(
        name="Usuário resetado:",
        value=f"{usuario.mention}",
        inline=False
    )
    embed.add_field(
        name="Saldo anterior:",
        value=f"**R$ {old_balance:,.2f}**",
        inline=False
    )
    embed.add_field(
        name="Novo saldo:",
        value="**R$ 0,00**",
        inline=False
    )

    embed.set_thumbnail(url=usuario.display_avatar.url)
    embed.set_footer(
        text="Sistema de economia",
        icon_url=client.user.display_avatar.url
    )

    await interaction.response.send_message(embed=embed)


@client.tree.command()
async def resetsaldoall(
        interaction: discord.Interaction
):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "❌ Você não tem permissão para resetar todos os saldos.",
            ephemeral=True
        )
        return

    client.cursor.execute('SELECT COUNT(*), SUM(balance) FROM economy WHERE balance > 0')
    result = client.cursor.fetchone()
    total_users = result[0]
    total_balance = result[1] / 100 if result[1] else 0  # Convert to reais

    client.cursor.execute('''
        UPDATE economy 
        SET balance = 0
        WHERE balance > 0
    ''')
    client.conn.commit()

    embed = discord.Embed(
        title="🔄 Reset Global de Saldos",
        description=f"Todos os saldos foram resetados por {interaction.user.mention}",
        color=discord.Color.red()
    )

    embed.add_field(
        name="Total de usuários afetados:",
        value=f"**{total_users}** usuários",
        inline=False
    )
    embed.add_field(
        name="Total de dinheiro removido:",
        value=f"**R$ {total_balance:,.2f}**",
        inline=False
    )
    embed.add_field(
        name="Novo saldo de todos:",
        value="**R$ 0,00**",
        inline=False
    )

    embed.set_footer(
        text="Sistema de economia",
        icon_url=client.user.display_avatar.url
    )

    await interaction.response.send_message(
        "⚠️ **ATENÇÃO!** Você tem certeza que deseja resetar o saldo de todos os usuários?\n"
        "Esta ação não pode ser desfeita!\n"
        "Reaja com ✅ para confirmar ou ❌ para cancelar.",
        embed=embed
    )

    message = await interaction.original_response()
    await message.add_reaction("✅")
    await message.add_reaction("❌")

    def check(reaction, user):
        return user == interaction.user and str(reaction.emoji) in ["✅", "❌"]

    try:
        reaction, user = await client.wait_for('reaction_add', timeout=30.0, check=check)

        if str(reaction.emoji) == "✅":
            client.cursor.execute('UPDATE economy SET balance = 0')
            client.conn.commit()

            await message.edit(content="✅ Todos os saldos foram resetados com sucesso!", embed=embed)
        else:
            await message.edit(content="❌ Operação cancelada.", embed=None)

    except asyncio.TimeoutError:
        await message.edit(content="⏰ Tempo esgotado. Operação cancelada.", embed=None)

    await message.clear_reactions()


@client.tree.command()
async def removepercent(
        interaction: discord.Interaction,
        usuario: discord.Member,
        porcentagem: float
):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "❌ Você não tem permissão para remover saldo.",
            ephemeral=True
        )
        return

    if porcentagem <= 0 or porcentagem > 100:
        await interaction.response.send_message(
            "❌ A porcentagem deve estar entre 0 e 100.",
            ephemeral=True
        )
        return

    ensure_user_exists(usuario.id)

    # Get current balance
    client.cursor.execute('SELECT balance FROM economy WHERE user_id = ?', (usuario.id,))
    current_balance = client.cursor.fetchone()[0]  # This is in cents

    # Calculate amount to remove
    amount_to_remove = int(current_balance * (porcentagem / 100))
    new_balance = current_balance - amount_to_remove

    # Update the balance
    client.cursor.execute('''
        UPDATE economy 
        SET balance = ?
        WHERE user_id = ?
    ''', (new_balance, usuario.id))
    client.conn.commit()

    embed = discord.Embed(
        title="💰 Saldo Removido (Porcentagem)",
        description=f"Saldo removido por {interaction.user.mention}",
        color=discord.Color.red()
    )

    embed.add_field(
        name="Usuário:",
        value=f"{usuario.mention}",
        inline=False
    )
    embed.add_field(
        name="Porcentagem removida:",
        value=f"**{porcentagem}%**",
        inline=False
    )
    embed.add_field(
        name="Saldo anterior:",
        value=f"**R$ {(current_balance / 100):,.2f}**",
        inline=False
    )
    embed.add_field(
        name="Valor removido:",
        value=f"**R$ {(amount_to_remove / 100):,.2f}**",
        inline=False
    )
    embed.add_field(
        name="Novo saldo:",
        value=f"**R$ {(new_balance / 100):,.2f}**",
        inline=False
    )

    embed.set_thumbnail(url=usuario.display_avatar.url)
    embed.set_footer(
        text="Sistema de economia",
        icon_url=client.user.display_avatar.url
    )

    await interaction.response.send_message(embed=embed)


@client.tree.command()
async def ranking(interaction: discord.Interaction):
    client.cursor.execute('''
        SELECT user_id, balance, 
        RANK() OVER (ORDER BY balance DESC) as rank_position
        FROM economy 
        WHERE balance > 0
    ''')
    all_rankings = client.cursor.fetchall()

    user_rank = None
    user_balance = 0
    for rank in all_rankings:
        if rank[0] == interaction.user.id:
            user_rank = rank[2]
            user_balance = rank[1]
            break

    top_10 = all_rankings[:10]

    embed = discord.Embed(
        title="🏆 Ranking de Riqueza em Deadcoins",
        description="Os usuários mais ricos do servidor",
        color=discord.Color.gold()
    )

    rank_text = ""
    for user_id, balance, position in top_10:
        try:
            member = await interaction.guild.fetch_member(user_id)
            name = member.display_name

            if position == 1:
                medal = "🥇"
            elif position == 2:
                medal = "🥈"
            elif position == 3:
                medal = "🥉"
            else:
                medal = "👑"

            rank_text += f"{medal} **{position}º** {name}\n"
            rank_text += f"└ {balance / 100:,.2f} Deadcoins\n\n"

        except discord.NotFound:
            continue

    embed.add_field(
        name="Top 10 Usuários",
        value=rank_text if rank_text else "Nenhum usuário encontrado.",
        inline=False
    )

    if user_rank and user_rank > 10:
        embed.add_field(
            name="Sua Posição",
            value=f"🎯 Você está em **{user_rank}º** lugar\n└ R$ {user_balance / 100:,.2f}",
            inline=False
        )
    elif not user_rank:
        embed.add_field(
            name="Sua Posição",
            value="❌ Você ainda não possui saldo no banco.",
            inline=False
        )

    client.cursor.execute('''
        SELECT COUNT(*) as total_users, 
        SUM(balance) as total_money 
        FROM economy 
        WHERE balance > 0
    ''')
    total_users, total_money = client.cursor.fetchone()

    if total_money:
        stats = (
            f"👥 Total de usuários: **{total_users}**\n"
            f"💰 Dinheiro em circulação: **R$ {total_money / 100:,.2f}**"
        )
        embed.add_field(name="Estatísticas", value=stats, inline=False)

    embed.set_thumbnail(url=interaction.guild.icon.url if interaction.guild.icon else None)
    embed.set_footer(
        text="Sistema de economia",
        icon_url=client.user.display_avatar.url
    )

    await interaction.response.send_message(embed=embed)


@client.tree.command()
async def sacar(
        interaction: discord.Interaction,
        valor: float
):
    # Verifica se o valor é positivo e maior que o mínimo
    if valor <= 0:
        await interaction.response.send_message(
            "❌ O valor do saque deve ser maior que zero.",
            ephemeral=True
        )
        return

    if valor < 50000:
        await interaction.response.send_message(
            "❌ O valor mínimo para saque é de 50.000,00 Deadcoins.",
            ephemeral=True
        )
        return

    # Garante que o usuário existe no banco
    ensure_user_exists(interaction.user.id)

    # Converte o valor para centavos para armazenamento no banco
    valor_cents = int(valor * 100)

    # Verifica o saldo do usuário
    client.cursor.execute('SELECT balance FROM economy WHERE user_id = ?', (interaction.user.id,))
    current_balance = client.cursor.fetchone()[0]

    # Verifica se o usuário tem saldo suficiente
    if current_balance < valor_cents:
        await interaction.response.send_message(
            f"❌ Você não tem saldo suficiente para sacar **R$ {valor:,.2f}**.",
            ephemeral=True
        )
        return

    # Realiza a atualização do saldo
    client.cursor.execute('''
        UPDATE economy 
        SET balance = balance - ?
        WHERE user_id = ?
    ''', (valor_cents, interaction.user.id))
    client.conn.commit()

    # Criar o embed de comprovante
    embed = discord.Embed(
        title="✅ Comprovante de Saque",
        description=f"Você realizou um saque de **{valor:,.2f} Deadcoins**.",
        color=discord.Color.green()
    )
    embed.add_field(name="Usuário", value=interaction.user.display_name, inline=False)
    embed.add_field(name="Valor", value=f"R$ {valor:,.2f}", inline=False)
    embed.set_footer(text=f"ID da transação: {interaction.id} | {datetime.now().strftime('%H:%M')}")

    # Enviar o embed nas DMs do usuário
    try:
        await interaction.user.send(embed=embed)
    except discord.Forbidden:
        await interaction.followup.send(
            "⚠️ Não foi possível enviar o comprovante no seu DM devido às suas configurações de privacidade.",
            ephemeral=True
        )

    # Enviar o embed no canal específico (ID: 1325644185264717844)
    canal_id = 1325644185264717844
    canal = client.get_channel(canal_id)
    if canal:
        await canal.send(embed=embed)

    # Responder no chat do comando
    await interaction.response.send_message(
        "✅ Seu saque foi realizado com sucesso! Verifique seu DM para o comprovante.",
        ephemeral=True
    )


@client.tree.command()
async def enviar(
        interaction: discord.Interaction,
        usuario: discord.Member,
        valor: float
):
    # Verifica condições básicas
    if usuario.bot:
        await interaction.response.send_message(
            "❌ Você não pode enviar dinheiro para um bot.",
            ephemeral=True
        )
        return

    if usuario.id == interaction.user.id:
        await interaction.response.send_message(
            "❌ Você não pode enviar dinheiro para si mesmo.",
            ephemeral=True
        )
        return

    if valor <= 0:
        await interaction.response.send_message(
            "❌ O valor deve ser maior que zero.",
            ephemeral=True
        )
        return

    # Responde imediatamente enquanto processa
    await interaction.response.defer(ephemeral=True)

    # Garante que ambos os usuários existem no banco
    ensure_user_exists(interaction.user.id)
    ensure_user_exists(usuario.id)

    valor_cents = int(valor * 100)

    # Verifica saldo do remetente
    client.cursor.execute('SELECT balance FROM economy WHERE user_id = ?', (interaction.user.id,))
    sender_balance = client.cursor.fetchone()[0]

    if sender_balance < valor_cents:
        await interaction.followup.send(
            "❌ Você não possui saldo suficiente para esta transferência.",
            ephemeral=True
        )
        return

    # Realiza a transferência
    client.cursor.execute('''
        UPDATE economy 
        SET balance = balance - ?
        WHERE user_id = ?
    ''', (valor_cents, interaction.user.id))

    client.cursor.execute('''
        UPDATE economy 
        SET balance = balance + ?
        WHERE user_id = ?
    ''', (valor_cents, usuario.id))

    client.conn.commit()

    # Criar o embed de comprovante
    embed = discord.Embed(
        title="✅ Comprovante de Transferência",
        description=f"Você enviou **{valor:,.2f} Deadcoins** para {usuario.display_name}.",
        color=discord.Color.green()
    )
    embed.add_field(name="De", value=interaction.user.display_name, inline=False)
    embed.add_field(name="Para", value=usuario.display_name, inline=False)
    embed.add_field(name="Valor", value=f"R$ {valor:,.2f}", inline=False)
    embed.set_footer(text=f"ID da transação: {interaction.id} | {datetime.now().strftime('%H:%M')}")

    # Enviar o embed nas DMs dos dois usuários
    try:
        await interaction.user.send(embed=embed)
    except discord.Forbidden:
        await interaction.followup.send(
            "⚠️ Não foi possível enviar o comprovante no seu DM devido às suas configurações de privacidade.",
            ephemeral=True
        )

    try:
        await usuario.send(embed=embed)
    except discord.Forbidden:
        await interaction.followup.send(
            f"⚠️ Não foi possível enviar o comprovante para {usuario.mention} devido às configurações de privacidade.",
            ephemeral=True
        )

    # Responder no chat do comando
    await interaction.followup.send(
        "✅ Transferência realizada com sucesso! Verifique seu DM para o comprovante.",
        ephemeral=True
    )


@client.tree.command()
async def ajuda(
        interaction: discord.Interaction,
        comando: str = None
):
    """Mostra informações sobre os comandos disponíveis"""

    # Dictionary with command explanations
    comandos = {
        "saldo": {
            "uso": "/saldo [usuário]",
            "desc": "Consulta o saldo de um usuário. Se nenhum usuário for especificado, mostra seu próprio saldo.",
            "explicacao_detalhada": """
                - Este comando permite verificar o saldo de contas
                - O parâmetro [usuário] é opcional (indicado pelos colchetes)
                - Se você não mencionar nenhum usuário, mostrará seu próprio saldo
                - Se você for administrador, pode verificar o saldo de qualquer pessoa
                - Se não for administrador, só pode ver seu próprio saldo
                - O saldo é mostrado em formato R$ 0,00
                - A resposta é enviada de forma privada (apenas você vê)
                - Inclui um embed com avatar do usuário consultado
            """,
            "exemplo": "/saldo @usuário",
            "permissão": "Qualquer um pode ver seu próprio saldo. Administradores podem ver o saldo de outros."
        },
        "addsaldo": {
            "uso": "/addsaldo <usuário> <quantidade>",
            "desc": "Adiciona uma quantidade específica ao saldo de um usuário.",
            "explicacao_detalhada": """
                - Exclusivo para administradores
                - Adiciona dinheiro à conta de um usuário específico
                - O parâmetro <usuário> é obrigatório e deve ser uma menção (@)
                - A <quantidade> deve ser um número positivo (ex: 100.50)
                - Aceita valores com até 2 casas decimais
                - A quantidade é somada ao saldo atual do usuário
                - Gera um embed mostrando:
                  * Quem adicionou o saldo
                  * Para qual usuário
                  * Quantidade adicionada
                - A operação é pública (todos podem ver)
            """,
            "exemplo": "/addsaldo @usuário 100.50",
            "permissão": "Apenas administradores"
        },
        "removesaldo": {
            "uso": "/removesaldo <usuário> <quantidade>",
            "desc": "Remove uma quantidade específica do saldo de um usuário.",
            "explicacao_detalhada": """
                - Exclusivo para administradores
                - Remove dinheiro da conta de um usuário específico
                - O parâmetro <usuário> é obrigatório e deve ser uma menção (@)
                - A <quantidade> deve ser um número positivo
                - Verifica se o usuário tem saldo suficiente antes de remover
                - Se não houver saldo suficiente, a operação é cancelada
                - Gera um embed mostrando:
                  * Quem removeu o saldo
                  * De qual usuário
                  * Quantidade removida
                - A operação é pública (todos podem ver)
            """,
            "exemplo": "/removesaldo @usuário 50.25",
            "permissão": "Apenas administradores"
        },
        "resetsaldo": {
            "uso": "/resetsaldo <usuário>",
            "desc": "Reseta o saldo de um usuário específico para zero.",
            "explicacao_detalhada": """
                - Exclusivo para administradores
                - Zera completamente o saldo de um usuário específico
                - O parâmetro <usuário> é obrigatório e deve ser uma menção (@)
                - Mostra o saldo anterior antes de zerar
                - Gera um embed com:
                  * Quem resetou o saldo
                  * Usuário afetado
                  * Saldo anterior
                  * Novo saldo (R$ 0,00)
                - A operação é pública (todos podem ver)
            """,
            "exemplo": "/resetsaldo @usuário",
            "permissão": "Apenas administradores"
        },
        "resetsaldoall": {
            "uso": "/resetsaldoall",
            "desc": "Reseta o saldo de todos os usuários para zero.",
            "explicacao_detalhada": """
                - Exclusivo para administradores
                - Zera o saldo de TODOS os usuários do servidor
                - Requer confirmação através de reações (✅ ou ❌)
                - Tem timeout de 30 segundos para confirmar
                - Mostra estatísticas antes do reset:
                  * Total de usuários afetados
                  * Total de dinheiro que será removido
                - Se confirmado, zera todos os saldos
                - Se cancelado ou timeout, mantém os saldos
                - A operação é pública (todos podem ver)
            """,
            "exemplo": "/resetsaldoall",
            "permissão": "Apenas administradores"
        },
        "removepercent": {
            "uso": "/removepercent <usuário> <porcentagem>",
            "desc": "Remove uma porcentagem específica do saldo de um usuário.",
            "explicacao_detalhada": """
                - Exclusivo para administradores
                - Remove uma porcentagem específica do saldo
                - O parâmetro <usuário> é obrigatório e deve ser uma menção (@)
                - A <porcentagem> deve ser entre 0 e 100
                - Calcula automaticamente o valor a ser removido
                - Mostra no embed:
                  * Saldo anterior
                  * Porcentagem removida
                  * Valor removido
                  * Novo saldo
                - A operação é pública (todos podem ver)
            """,
            "exemplo": "/removepercent @usuário 50",
            "permissão": "Apenas administradores"
        },
        "ranking": {
            "uso": "/ranking",
            "desc": "Mostra o ranking dos usuários mais ricos do servidor.",
            "explicacao_detalhada": """
                - Disponível para todos os usuários
                - Mostra os 10 usuários mais ricos do servidor
                - Indica posições especiais com emojis:
                  * 🥇 1º lugar
                  * 🥈 2º lugar
                  * 🥉 3º lugar
                  * 👑 demais posições
                - Se você não estiver no top 10, mostra sua posição
                - Exibe estatísticas gerais:
                  * Total de usuários com saldo
                  * Total de dinheiro em circulação
                - A resposta é pública (todos podem ver)
            """,
            "exemplo": "/ranking",
            "permissão": "Qualquer um pode usar"
        },
        "enviar": {
            "uso": "/enviar <usuário> <valor>",
            "desc": "Transfere uma quantidade específica do seu saldo para outro usuário.",
            "explicacao_detalhada": """
                - Disponível para todos os usuários
                - Permite transferir dinheiro entre usuários
                - Validações:
                  * Não pode enviar para bots
                  * Não pode enviar para si mesmo
                  * Valor deve ser positivo
                  * Deve ter saldo suficiente
                - Gera um comprovante visual com:
                  * Remetente e destinatário
                  * Valor transferido
                  * ID da transação
                  * Hora da transferência
                - Envia o comprovante no DM dos envolvidos
                - A confirmação é privada (apenas você vê)
            """,
            "exemplo": "/enviar @usuário 100.50",
            "permissão": "Qualquer um pode usar"
        },
        "sacar": {
            "uso": "/sacar <valor>",
            "desc": "Saca uma quantidade específica do seu saldo.",
            "explicacao_detalhada": """
                - Disponível para todos os usuários
                - Permite sacar dinheiro da sua conta
                - O <valor> deve ser positivo
                - Validações:
                  * Valor deve ser maior que zero
                  * Deve ter saldo suficiente
                - Gera um comprovante visual com:
                  * Seu nome
                  * Valor sacado
                  * ID da transação
                  * Hora do saque
                - Envia o comprovante no seu DM
                - A confirmação é privada (apenas você vê)
            """,
            "exemplo": "/sacar 100.50",
            "permissão": "Qualquer um pode usar"
        }
    }

    if comando is None:
        # Show list of all commands
        embed = discord.Embed(
            title="📚 Lista de Comandos",
            description="Use `/ajuda <comando>` para ver informações detalhadas sobre um comando específico.",
            color=discord.Color.blue()
        )

        for cmd, info in comandos.items():
            embed.add_field(
                name=f"/{cmd}",
                value=info["desc"],
                inline=False
            )

    elif comando.lower() in comandos:
        # Show detailed info about specific command
        cmd_info = comandos[comando.lower()]
        embed = discord.Embed(
            title=f"📖 Ajuda: /{comando}",
            description=cmd_info["desc"],
            color=discord.Color.blue()
        )

        embed.add_field(name="Uso", value=f"`{cmd_info['uso']}`", inline=False)
        embed.add_field(name="Exemplo", value=f"`{cmd_info['exemplo']}`", inline=False)
        embed.add_field(name="Permissão", value=cmd_info["permissão"], inline=False)

    else:
        await interaction.response.send_message(
            f"❌ Comando `{comando}` não encontrado. Use `/ajuda` para ver a lista de comandos disponíveis.",
            ephemeral=True
        )
        return

    embed.set_footer(
        text="Sistema de economia",
        icon_url=client.user.display_avatar.url
    )

    await interaction.response.send_message(embed=embed, ephemeral=True)


@client.tree.command()
async def ajjsac(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "❌ Você não tem permissão",
            ephemeral=True
        )
        return

    embed = discord.Embed(
        title="💸 Como Usar o Comando de Saque",
        description="Explicação detalhada sobre como funciona o comando `/sacar`",
        color=discord.Color.green()
    )

    embed.add_field(
        name="📝 Formato do Comando",
        value="```/sacar <valor>```\nExemplo: `/sacar 100.50`",
        inline=False
    )

    embed.add_field(
        name="✨ Características",
        value="""
• O valor deve ser positivo (maior que zero)
• Você deve ter saldo suficiente para sacar
• O valor pode ter até 2 casas decimais
• O saque é descontado imediatamente do seu saldo
• Você recebe um comprovante visual no seu DM
""",
        inline=False
    )

    embed.add_field(
        name="🧾 Comprovante",
        value="""O comprovante de saque inclui:
• Seu nome
• Valor sacado
• ID único da transação
• Data e hora do saque
• Design visual profissional
""",
        inline=False
    )

    embed.add_field(
        name="⚠️ Importante",
        value="""
• Certifique-se de ter suas DMs abertas para receber o comprovante
• O saque não pode ser desfeito
• Em caso de erro, contate um administrador
""",
        inline=False
    )

    embed.set_footer(text="Sistema de Economia")
    await interaction.response.send_message(embed=embed)


@client.tree.command()
async def ajjsald(interaction: discord.Interaction):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "❌ Você não tem permissão",
            ephemeral=True
        )
        return

    embed = discord.Embed(
        title="💸 Como Usar o Sistema de Transferência",
        description="Explicação detalhada sobre como funciona o comando `/enviar`",
        color=discord.Color.blue()
    )

    embed.add_field(
        name="📝 Formato do Comando",
        value="```/enviar <@usuário> <valor>```\nExemplo: `/enviar @João 100.50`",
        inline=False
    )

    embed.add_field(
        name="✨ Características",
        value="""
• Transferência instantânea entre usuários
• O valor deve ser positivo (maior que zero)
• Você deve ter saldo suficiente
• O valor pode ter até 2 casas decimais
• A transferência é processada imediatamente
• Ambos recebem um comprovante visual no DM
""",
        inline=False
    )

    embed.add_field(
        name="🚫 Limitações",
        value="""
• Não é possível enviar dinheiro para bots
• Não é possível enviar dinheiro para si mesmo
• Não é possível enviar mais do que você possui
• Não é possível enviar valores negativos
""",
        inline=False
    )

    embed.add_field(
        name="🧾 Comprovante",
        value="""O comprovante de transferência inclui:
• Nome do remetente
• Nome do destinatário
• Valor transferido
• ID único da transação
• Data e hora da transferência
• Design visual profissional
""",
        inline=False
    )

    embed.add_field(
        name="⚠️ Importante",
        value="""
• Certifique-se de ter suas DMs abertas para receber o comprovante
• Verifique bem o usuário antes de transferir
• A transferência não pode ser desfeita
• Em caso de erro, contate um administrador
""",
        inline=False
    )

    embed.set_footer(text="Sistema de Economia")
    await interaction.response.send_message(embed=embed)


async def send_daily_ranking():
    stored_message = None
    while True:
        try:
            channel = client.get_channel(1325564899879026758)

            if channel:
                client.cursor.execute('''
                    SELECT user_id, balance, 
                    RANK() OVER (ORDER BY balance DESC) as rank_position
                    FROM economy 
                    WHERE balance > 0
                ''')
                all_rankings = client.cursor.fetchall()
                top_10 = all_rankings[:10]

                embed = discord.Embed(
                    title="🏆 Ranking Diário de Deadcoins",
                    description="Os usuários mais ricos do servidor",
                    color=discord.Color.gold()
                )

                rank_text = ""
                for user_id, balance, position in top_10:
                    try:
                        member = await channel.guild.fetch_member(user_id)
                        name = member.display_name

                        if position == 1:
                            medal = "🥇"
                        elif position == 2:
                            medal = "🥈"
                        elif position == 3:
                            medal = "🥉"
                        else:
                            medal = "👑"

                        rank_text += f"{medal} **{position}º** {name}\n"
                        rank_text += f"└ Ð {balance / 100:,.2f}\n\n"

                    except discord.NotFound:
                        continue

                embed.add_field(
                    name="Top 10 Usuários",
                    value=rank_text if rank_text else "Nenhum usuário encontrado.",
                    inline=False
                )

                client.cursor.execute('''
                    SELECT COUNT(*) as total_users, 
                    SUM(balance) as total_money 
                    FROM economy 
                    WHERE balance > 0
                ''')
                total_users, total_money = client.cursor.fetchone()

                if total_money:
                    stats = (
                        f"👥 Total de usuários: **{total_users}**\n"
                        f"💰 Deadcoins em circulação: **Ð {total_money / 100:,.2f}**"
                    )
                    embed.add_field(name="Estatísticas", value=stats, inline=False)

                embed.set_thumbnail(url=channel.guild.icon.url if channel.guild.icon else None)
                embed.set_footer(
                    text="Sistema de economia • Ranking Diário",
                    icon_url=client.user.display_avatar.url
                )

                if stored_message is None:
                    stored_message = await channel.send(embed=embed)
                else:
                    try:
                        await stored_message.edit(embed=embed)
                    except discord.NotFound:
                        stored_message = await channel.send(embed=embed)

        except Exception as e:
            print(f"Erro ao atualizar ranking diário: {e}")

        await asyncio.sleep(86400)


@client.tree.command()
async def except_user(
        interaction: discord.Interaction,
        usuario: discord.Member
):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "❌ Você não tem permissão para usar este comando.",
            ephemeral=True
        )
        return

    client.cursor.execute('INSERT OR REPLACE INTO excepted_users (user_id) VALUES (?)', (usuario.id,))
    client.conn.commit()

    embed = discord.Embed(
        title="⛔ Usuário Excetuado",
        description=f"{usuario.mention} não receberá mais moedas automáticas",
        color=discord.Color.red()
    )
    embed.set_footer(text="Sistema de Economia")

    await interaction.response.send_message(embed=embed)


# Add new unexcept command for removing users from exception list
@client.tree.command()
async def unexcept_user(
        interaction: discord.Interaction,
        usuario: discord.Member
):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "❌ Você não tem permissão para usar este comando.",
            ephemeral=True
        )
        return

    client.cursor.execute('DELETE FROM excepted_users WHERE user_id = ?', (usuario.id,))
    client.conn.commit()

    embed = discord.Embed(
        title="✅ Exceção Removida",
        description=f"{usuario.mention} voltará a receber moedas automáticas",
        color=discord.Color.green()
    )
    embed.set_footer(text="Sistema de Economia")

    await interaction.response.send_message(embed=embed)

sdk = mercadopago.SDK("APP_USR-3127370453049654-011114-5e758cc211d62f5db3005733cc36143c-170195579")

@client.tree.command()
async def comprar(interaction: discord.Interaction, reais: float):

    if reais < 1:
        await interaction.response.send_message("❌ Valor mínimo: R$ 1,00", ephemeral=True)
        return

    deadcoins = int(reais * 1000)

    preference_data = {
        "items": [
            {
                "title": f"{deadcoins} Deadcoins",
                "quantity": 1,
                "currency_id": "BRL",
                "unit_price": reais
            }
        ],
        "back_urls": {
            "success": "https://seu-site.com/success",
            "failure": "https://seu-site.com/failure"
        },
        "external_reference": f"{interaction.user.id}"
    }

    preference_response = sdk.preference().create(preference_data)
    payment_url = preference_response["response"]["init_point"]

    embed = discord.Embed(
        title="🛒 Comprar Deadcoins",
        description=f"Você está comprando {deadcoins:,} Deadcoins por R$ {reais:.2f}",
        color=discord.Color.blue()
    )
    embed.add_field(name="Link de Pagamento", value=f"[Clique aqui para pagar]({payment_url})")
    embed.set_footer(text="O pagamento será processado pelo Mercado Pago")

    await interaction.response.send_message(embed=embed, ephemeral=True)


@client.event
async def on_webhook(data):
    if data["type"] == "payment" and data["status"] == "approved":
        user_id = int(data["external_reference"])
        amount = float(data["transaction_amount"])
        deadcoins = int(amount * 1000)

        client.cursor.execute('''
            UPDATE economy 
            SET balance = balance + ?
            WHERE user_id = ?
        ''', (deadcoins * 100, user_id))
        client.conn.commit()

        user = await client.fetch_user(user_id)
        if user:
            embed = discord.Embed(
                title="✅ Pagamento Confirmado",
                description=f"Você recebeu {deadcoins:,} Deadcoins!",
                color=discord.Color.green()
            )
            try:
                await user.send(embed=embed)
            except:
                pass

@client.event
async def on_ready():
    print(f'Bot está online como {client.user}')
    if client.voice_check_task is None:
        client.voice_check_task = client.loop.create_task(client.check_voice_channels())
    client.loop.create_task(send_daily_ranking())


client.run('MTMyNzA2MzAwNDk0NDI3MzQzOQ.GjW_ED.ZCSldcjS34r5q-7ywX3CvdTQHhwSBsFeFPLnv8')
