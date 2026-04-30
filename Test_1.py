def student_grade_manager():
    students = {}
    #testing
    #uday

    def add_student(name, grades):
        students[name] = grades

    def average(grades):
        return sum(grades) / len(grades) if grades else 0

    def letter_grade(avg):
        if avg >= 90: return 'A'
        elif avg >= 80: return 'B'
        elif avg >= 70: return 'C'
        elif avg >= 60: return 'D'
        else: return 'F'

    def top_student():
        return max(students, key=lambda name: average(students[name]))

    def failing_students():
        return [name for name, grades in students.items() if average(grades) < 60]

    def class_average():
        all_grades = [g for grades in students.values() for g in grades]
        return sum(all_grades) / len(all_grades) if all_grades else 0

    def print_report():
        print("=" * 45)
        print(f"{'STUDENT GRADE REPORT':^45}")
        print("=" * 45)
        for name, grades in students.items():
            avg = average(grades)
            grade = letter_grade(avg)
            print(f"{name:<20} Avg: {avg:>6.2f}  Grade: {grade}")
        print("-" * 45)
        print(f"{'Class Average:':<20} {class_average():>6.2f}")
        print(f"{'Top Student:':<20} {top_student()}")
        failing = failing_students()
        if failing:
            print(f"Failing Students:    {', '.join(failing)}")
        else:
            print("No failing students!")
        print("=" * 45)

    # Add sample students
    add_student("Alice",   [95, 88, 92, 97, 91])
    add_student("Bob",     [72, 65, 70, 68, 74])
    add_student("Charlie", [55, 58, 50, 62, 48])
    add_student("Diana",   [88, 91, 85, 90, 94])
    add_student("Eve",     [40, 55, 45, 50, 38])

    print_report()

student_grade_manager()
