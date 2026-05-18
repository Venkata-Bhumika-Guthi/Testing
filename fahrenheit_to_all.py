def celsius_to_all(c):
    print(f"Celsius:    {c}°C")
    print(f"Fahrenheit: {c * 9/5 + 32}°F")
    print(f"Kelvin:     {c + 273.15}K")

def fahrenheit_to_all(f):
    print(f"Fahrenheit: {f}°F")
    print(f"Celsius:    {f - 32} * 5/9 = {(f - 32) * 5/9:.2f}°C")
    print(f"Kelvin:     {(f - 32) * 5/9 + 273.15:.2f}K")

celsius_to_all(100)
print()
fahrenheit_to_all(212)
