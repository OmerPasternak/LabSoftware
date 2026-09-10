"""
matrix_multiplication.py
------------------------
Educational demonstration and implementation of matrix multiplication in pure Python.

Key Rule:
To multiply matrix A (shape m x k) by matrix B (shape k x n):
  - The number of columns in A (k) MUST equal the number of rows in B (k).
  - The resulting matrix C has shape (m x n).
  - Each element C[i][j] is the dot product of row i from A and column j from B:
      C[i][j] = sum(A[i][p] * B[p][j] for p in range(k))
"""

from typing import List

# Type alias for a 2D matrix of numbers
Matrix = List[List[float]]


def get_shape(matrix: Matrix) -> tuple[int, int]:
    """Returns (rows, cols) for a 2D matrix."""
    if not matrix or not matrix[0]:
        return (0, 0)
    return len(matrix), len(matrix[0])


def print_matrix(matrix: Matrix, name: str = "") -> None:
    """Pretty-prints a matrix with optional label."""
    if name:
        print(f"{name}:")
    for row in matrix:
        print("  [" + ", ".join(f"{val:6.2f}" if isinstance(val, float) else f"{val:4}" for val in row) + " ]")
    print()


def matrix_multiply(A: Matrix, B: Matrix) -> Matrix:
    """
    Standard matrix multiplication using three nested loops.
    
    Args:
        A: Matrix of shape (m, k)
        B: Matrix of shape (k, n)
        
    Returns:
        C: Matrix of shape (m, n) where C = A x B
        
    Raises:
        ValueError: If matrix dimensions are incompatible.
    """
    rows_A, cols_A = get_shape(A)
    rows_B, cols_B = get_shape(B)

    # Validate inner dimensions
    if cols_A != rows_B:
        raise ValueError(
            f"Cannot multiply matrices: columns of A ({cols_A}) "
            f"must match rows of B ({rows_B})."
        )

    # Initialize result matrix C with dimensions (rows_A x cols_B) with zeros
    C = [[0 for _ in range(cols_B)] for _ in range(rows_A)]

    # Triple nested loop:
    # i loops over rows of A
    # j loops over columns of B
    # k loops over the shared dimension (columns of A / rows of B)
    for i in range(rows_A):
        for j in range(cols_B):
            total = 0
            for p in range(cols_A):
                total += A[i][p] * B[p][j]
            C[i][j] = total

    return C


def matrix_multiply_verbose(A: Matrix, B: Matrix) -> Matrix:
    """
    Multiplies two matrices and prints out the exact step-by-step arithmetic
    for each cell to verify and visualize understanding.
    """
    rows_A, cols_A = get_shape(A)
    rows_B, cols_B = get_shape(B)

    if cols_A != rows_B:
        raise ValueError(f"Incompatible dimensions: ({rows_A}x{cols_A}) and ({rows_B}x{cols_B})")

    print(f"Multiplying Matrix A ({rows_A}x{cols_A}) by Matrix B ({rows_B}x{cols_B}):")
    print_matrix(A, "Matrix A")
    print_matrix(B, "Matrix B")
    print("-" * 60)
    print("Step-by-step element calculation:")

    C = [[0 for _ in range(cols_B)] for _ in range(rows_A)]

    for i in range(rows_A):
        for j in range(cols_B):
            terms = []
            values = []
            total = 0
            for p in range(cols_A):
                a_val = A[i][p]
                b_val = B[p][j]
                prod = a_val * b_val
                terms.append(f"({a_val} * {b_val})")
                values.append(str(prod))
                total += prod

            C[i][j] = total
            steps_formula = " + ".join(terms)
            steps_values = " + ".join(values)
            print(f"  C[{i}][{j}] = {steps_formula}")
            print(f"          = {steps_values} = {total}")

    print("-" * 60)
    print_matrix(C, "Result Matrix C (A x B)")
    return C


if __name__ == "__main__":
    print("=" * 60)
    print("DEMO 1: Detailed Step-by-Step Multiplication")
    print("=" * 60)
    
    # Example 1: 2x3 multiplied by 3x2 -> 2x2 result
    # A has 2 rows, 3 columns
    A1 = [
        [1, 2, 3],
        [4, 5, 6]
    ]
    # B has 3 rows, 2 columns
    B1 = [
        [7, 8],
        [9, 1],
        [2, 3]
    ]
    
    matrix_multiply_verbose(A1, B1)

    print("=" * 60)
    print("DEMO 2: Square Matrices (2x2 x 2x2)")
    print("=" * 60)
    A2 = [
        [2, 3],
        [1, 4]
    ]
    B2 = [
        [5, 1],
        [2, 6]
    ]
    matrix_multiply_verbose(A2, B2)

    print("=" * 60)
    print("DEMO 3: Dimension Mismatch Error Handling")
    print("=" * 60)
    try:
        # A is 2x3, B is 2x2 -> (cols_A=3 != rows_B=2)
        incompatible_A = [[1, 2, 3], [4, 5, 6]]
        incompatible_B = [[1, 2], [3, 4]]
        print(f"Attempting to multiply (2x3) with (2x2)...")
        matrix_multiply(incompatible_A, incompatible_B)
    except ValueError as err:
        print(f"Caught expected error: {err}")

