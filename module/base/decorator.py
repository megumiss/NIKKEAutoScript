from functools import wraps


class Config:
    func_list = {}

    @classmethod
    def when(cls, **kwargs):
        """
             Args:
                 **kwargs: Any option in NikkeConfig.

             Examples:
                 @Config.when(USE_ONE_CLICK_RETIREMENT=True)
                 def retire_ships(self, amount=None, rarity=None):
                     pass

                 @Config.when(USE_ONE_CLICK_RETIREMENT=False)
                 def retire_ships(self, amount=None, rarity=None):
                     pass
        """

        """
            当需要以两种配置调用同一个方法时
            在若干名称一样方法上面使用 @Config.when(类变量=值) 装饰器
        """

        from module.logger import logger
        options = kwargs

        def decorate(func):
            """
                方法名
            """
            name = func.__name__
            """
                options: 参数组
                func: 被 @Config.when 修饰的若干名称相同的方法
            """
            data = {'options': options, 'func': func}

            """
                类变量 func_list 记录所有被 @Config 修饰的方法
            """
            if name not in cls.func_list:
                cls.func_list[name] = [data]
            else:
                override = False
                for record in cls.func_list[name]:
                    """
                        当已经记录的方法和当前被修饰的方法一样时，且参数组一样时，用当前方法覆盖已经记录的方法
                        使用最后遍历的方法
                    """
                    if record['options'] == data['options']:
                        record['func'] = data['func']
                        override = True
                """
                   当参数组不一样时，在 func_list 中添加的新的记录
                """
                if not override:
                    cls.func_list[name].append(data)

            @wraps(func)
            def wrapper(self, *args, **kwargs):
                """
                    self:  被 @Config 修饰的方法本身
                    *args, **kwargs: 被修饰的方法调用时传入的参数

                    遍历所有名称相同的方法
                """
                for record in cls.func_list[name]:

                    """
                        flag为 配置 与 当前遍历的 方法 的装饰器参数组 是否相同
                    """

                    flag = [
                        value is None or
                        (
                            isinstance(value, (list, tuple, set)) and self.config.__getattribute__(key) in value
                        ) or
                        self.config.__getattribute__(key) == value
                        for key, value in record['options'].items()
                    ]

                    """
                        当前方法的装饰器参数与配置不完全相同时，跳过
                    """
                    if not all(flag):
                        continue

                    """
                        若完全相同则调用该方法
                    """
                    return record['func'](self, *args, **kwargs)

                """
                    若定义的若干方法的装饰器参数，没有与配置完全相同的时候，则调用当前遍历的方法
                """
                logger.warning(f'No option fits for {name}, using the last define func.')
                return func(self, *args, **kwargs)

            return wrapper

        return decorate

def has_cached_property(obj, name):
    """
    Check if a property is cached.

    Args:
        obj:
        name (str):
    """
    return name in obj.__dict__


def set_cached_property(obj, name, value):
    """
    Set a cached property.

    Args:
        obj:
        name (str):
        value:
    """
    obj.__dict__[name] = value


def del_cached_property(obj, name):
    """
    Delete a cached property safely.

    Args:
        obj:
        name (str):
    """
    try:
        del obj.__dict__[name]
    except KeyError:
        pass


def run_once(f):
    """
    Run a function only once, no matter how many times it has been called.

    Examples:
        @run_once
        def my_function(foo, bar):
            return foo + bar

        while 1:
            my_function()

    Examples:
        def my_function(foo, bar):
            return foo + bar

        action = run_once(my_function)
        while 1:
            action()
    """

    def wrapper(*args, **kwargs):
        if not wrapper.has_run:
            wrapper.has_run = True
            return f(*args, **kwargs)

    wrapper.has_run = False
    return wrapper
