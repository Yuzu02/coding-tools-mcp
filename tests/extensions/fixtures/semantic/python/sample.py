class Greeter:
    def hello(self, name: str) -> str:
        return format_name(name)


def format_name(name: str) -> str:
    return name.title()


def run() -> str:
    return Greeter().hello("world")
