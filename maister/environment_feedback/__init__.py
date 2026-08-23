"""The checks that decide whether a model can be built out of real bricks.

Three independent checkers, none of which involves a language model:

* ``ldr_validator``            - part numbers resolve; the file is well formed
* ``ldr_connectivity_checker`` - every part on a real stud; what holds together
* ``ldr_collision_checker``    - swept-volume overlap, measured off real shapes
"""
