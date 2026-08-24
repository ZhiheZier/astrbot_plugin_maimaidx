from textwrap import dedent


class UserNotFoundError(Exception):
    
    def __str__(self) -> str:
        return dedent('''
            未找到此玩家，请确保此玩家的用户名和水鱼查分器中的用户名相同。
            如未绑定，请前往水鱼查分器官网进行绑定
            https://www.diving-fish.com/maimaidx/prober/
        ''').strip()


class UserNotExistsError(Exception):

    def __str__(self) -> str:
        return '查询的用户不存在'


class UserDisabledQueryError(Exception):

    def __str__(self) -> str:
        return '该用户禁止了其他人获取数据或未同意用户协议。'


class TokenError(Exception):

    def __str__(self) -> str:
        return '开发者Token有误'


class TokenDisableError(Exception):

    def __str__(self) -> str:
        return '开发者Token被禁用'


class TokenNotFoundError(Exception):

    def __str__(self) -> str:
        return 'BOT 管理员尚未配置水鱼 OAuth 应用或开发者 Token'


class DivingFishOAuthError(TokenNotFoundError):
    """水鱼 OAuth 请求失败。继承旧异常以兼容现有调用方。"""

    message = '水鱼 OAuth 请求失败，请稍后重试'

    def __str__(self) -> str:
        return self.message


class DivingFishOAuthNotBoundError(DivingFishOAuthError):

    message = '尚未授权水鱼查分器，请发送「绑定水鱼」完成授权'


class DivingFishOAuthConfigError(DivingFishOAuthError):

    message = 'BOT 管理员配置的水鱼 OAuth 应用信息有误，请联系管理员检查'


class DivingFishOAuthPermissionError(DivingFishOAuthError):

    message = '水鱼 OAuth 授权缺少读取成绩权限，请重新绑定或联系 BOT 管理员'


class DivingFishOAuthRateLimitError(DivingFishOAuthError):

    message = '水鱼查分器查询次数已达上限，请稍后再试'


class DivingFishOAuthServiceError(DivingFishOAuthError):

    message = '水鱼 OAuth 服务暂时不可用，请稍后再试'


class DivingFishOAuthTokenExpiredError(DivingFishOAuthError):
    """仅供 API 层清除缓存并重试，不应直接展示给用户。"""

    message = '水鱼 OAuth 访问令牌已失效，请重试'


class DivingFishOAuthPendingError(DivingFishOAuthError):
    """设备码授权仍在等待用户操作。"""

    message = '正在等待用户完成水鱼授权'


class DivingFishOAuthBindingDeniedError(DivingFishOAuthError):

    message = '已取消水鱼查分器授权'


class DivingFishOAuthBindingExpiredError(DivingFishOAuthError):

    message = '水鱼查分器授权已超时，请重新发送「绑定水鱼」'


class DivingFishLegacyApiRetiredError(DivingFishOAuthError):

    message = '水鱼旧版开发者接口已停用，请联系 BOT 管理员配置 OAuth'


class MusicNotPlayError(Exception):
    
    def __str__(self) -> str:
        return '您未游玩该曲目'


class ServerError(Exception):

    def __str__(self) -> str:
        return '别名服务器错误，请联系插件开发者'


class EnterError(Exception):

    def __str__(self) -> str:
        return '参数输入错误'


class AliasesNotFoundError(Exception):
    
    def __str__(self) -> str:
        return '未找到别名'


class UnknownError(Exception):
    """未知错误"""
