import discord

class RaidRoleButtonView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="레이드 역할 관리", style=discord.ButtonStyle.primary, custom_id="raid_role_manage")
    async def raid_role_button(self, button: discord.ui.Button, interaction: discord.Interaction):
        pass

class RaidRoleDropdown(discord.ui.Select):
    def __init__(self, user_roles):
        from core.raid_data import raid_list
        
        # 유저가 가진 역할 이름들
        user_role_names = [role.name for role in user_roles]
        
        options = [
            discord.SelectOption(
                label=raid_name, 
                description=raid_name, 
                default=raid_name in user_role_names
            )
            for raid_name in raid_list
        ]
        
        super().__init__(
            placeholder="레이드 역할을 선택하세요...",
            min_values=0,
            max_values=len(options),
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        user = interaction.user
        
        try:
            current_roles = user.roles
            current_role_names = [role.name for role in current_roles]
            
            # 선택된 역할들
            selected_role_names = self.values
            
            # 모든 레이드 역할들 (드롭다운 옵션들)
            all_raid_role_names = [option.label for option in self.options]
            
            # 추가해야 할 역할들 (선택됐는데 현재 없는 역할)
            for role_name in selected_role_names:
                if role_name not in current_role_names:
                    role = discord.utils.get(guild.roles, name=role_name)
                    if role:
                        await user.add_roles(role)
            
            # 제거해야 할 역할들 (선택 안됐는데 현재 있는 역할)
            unselected_role_names = set(all_raid_role_names) - set(selected_role_names)
            for role_name in unselected_role_names:
                if role_name in current_role_names:
                    role = discord.utils.get(guild.roles, name=role_name)
                    if role:
                        await user.remove_roles(role)
                        
        except Exception as e:
            print(f"레이드 역할 관리 오류: {e}")

class RaidRoleDropdownView(discord.ui.View):
    def __init__(self, user_roles):
        super().__init__(timeout=300)
        self.add_item(RaidRoleDropdown(user_roles))