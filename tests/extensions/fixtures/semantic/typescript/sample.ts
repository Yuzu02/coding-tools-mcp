export class Greeter {
  hello(name: string): string {
    return formatName(name)
  }
}

export function formatName(name: string): string {
  return name.toUpperCase()
}

export function run(): string {
  return new Greeter().hello("world")
}

export interface Speaker {
  speak(value: string): string
}

export class LoudSpeaker implements Speaker {
  speak(value: string): string {
    return value.toUpperCase()
  }
}
