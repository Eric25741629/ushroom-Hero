from miner.planning.smart_planner import SmartPlanner, SmartState

def test_repro_with_pit():
    board = [
        [".", ".", ".", ".", ".", "."],
        [".", ".", ".", ".", ".", "."],
        [".", ".", ".", ".", ".", "."],
        [".", ".", ".", ".", ".", "."],
        [".", ".", ".", ".", ".", "R"],
        [".", "D", ".", ".", "D", "unreachable_empty"],
        ["D", "unreachable_empty", "R", "R", "unreachable_dirt", "reachable_pit"]
    ]
    
    internal_board = []
    for row in board:
        new_row = []
        for cell in row:
            if cell == ".": new_row.append("empty")
            elif cell == "D": new_row.append("dirt")
            elif cell == "R": new_row.append("rock")
            elif cell == "_": new_row.append("unreachable_empty")
            elif cell == "d": new_row.append("unreachable_dirt")
            elif cell == "*": new_row.append("reachable_pit")
            else: new_row.append(cell)
        internal_board.append(new_row)

    items = {'drill': 10, 'bomb': 10}
    planner = SmartPlanner(internal_board, 100, items)
    
    print("\nStarting solve...")
    result = planner.solve(beam_width=40, max_depth=10)
    
    print("\nResult:")
    print(f"OK: {result['ok']}")
    print(f"Cost: {result['total_cost']}")
    print(f"Pits: {result['remaining_pits']}")
    print("Steps:")
    for s in result['steps']:
        print(f"  {s}")

if __name__ == "__main__":
    test_repro_with_pit()