from __future__ import print_function
"""This module defines an ASE interface to SIESTA.

http://www.uam.es/departamentos/ciencias/fismateriac/siesta
"""
import os
from os.path import join, isfile, islink
import string
import numpy as np

from ase.units import Ry, eV
from ase.data import atomic_numbers
from ase.calculators.siesta.import_functions import read_rho, xv_to_atoms
from ase.calculators.calculator import FileIOCalculator, ReadError
from ase.calculators.calculator import Parameters, all_changes
from ase.calculators.siesta.parameters import PAOBasisBlock, Specie
from ase.calculators.siesta.parameters import format_fdf

meV = 0.001 * eV


class SiestaParameters(Parameters):
    """Parameters class for the calculator.
    Documented in BaseSiesta.__init__

    """
    def __init__(
            self,
            label='siesta',
            mesh_cutoff=200 * Ry,
            energy_shift=100 * meV,
            kpts=(1, 1, 1),
            xc='LDA',
            basis_set='DZP',
            spin='COLLINEAR',
            species=tuple(),
            pseudo_qualifier=None,
            pseudo_path=None,
            atoms=None,
            n_nodes=1,
            restart=None,
            ignore_bad_restart_file=False,
            siesta_executable=None,
            fdf_arguments=None):
        kwargs = locals()
        kwargs.pop('self')
        Parameters.__init__(self, **kwargs)


class BaseSiesta(FileIOCalculator):
    """Calculator interface to the SIESTA code.
    """
    allowed_basis_names = ['SZ', 'SZP', 'DZ', 'DZP']
    allowed_spins = ['UNPOLARIZED', 'COLLINEAR', 'FULL']
    allowed_xc = {}
    allowed_fdf_keywords = {}
    unit_fdf_keywords = {}

    implemented_properties = (
        'energy',
        'forces',
        'stress',
        'dipole',
        'eigenvalues',
        'density',
        'fermi_energy')

    # Dictionary of valid input vaiables.
    default_parameters = SiestaParameters()

    def __init__(self, **kwargs):
        """ASE interface to the SIESTA code.

        Parameters:
            -label        : The base head of all created files.
            -mesh_cutoff  : tuple of (value, energy_unit)
                            The mesh cutoff energy for determining number of
                            grid points.
            -energy_shift : tuple of (value, energy_unit)
                            The confining energy of the basis sets.
            -kpts         : Tuple of 3 integers, the k-points in different
                            directions.
            -xc           : The exchange-correlation potential. Can be set to
                            any allowed value for either the Siesta
                            XC.funtional or XC.authors keyword. Default "LDA"
            -basis_set    : "SZ"|"SZP"|"DZ"|"DZP", strings which specify the
                            type of functions basis set.
            -spin         : "UNPOLARIZED"|"COLLINEAR"|"FULL". The level of spin
                            description to be used.
            -species      : None|list of Specie objects. The species objects
                            can be used to to specify the basis set,
                            pseudopotential and whether the species is ghost.
                            The tag on the atoms object and the element is used
                            together to identify the species.
            -pseudo_path  : None|path. This path is where
                            pseudopotentials are taken from.
                            If None is given, then then the path given
                            in $SIESTA_PP_PATH will be used.
            -pseudo_qualifier: None|string. This string will be added to the
                            pseudopotential path that will be retrieved.
                            For hydrogen with qualifier "abc" the
                            pseudopotential "H.abc.psf" will be retrieved.
            -atoms        : The Atoms object.
            -n_nodes      : The number of nodes to use.
            -restart      : str.  Prefix for restart file.
                            May contain a directory.
                            Default is  None, don't restart.
            -ignore_bad_restart_file: bool.
                            Ignore broken or missing restart file.
                            By default, it is an error if the restart
                            file is missing or broken.
            -siesta_executable : path to the siesta executable, if None the
                            environment variable 'SIESTA' will be used.
            -fdf_arguments: Explicitly given fdf arguments. Dictonary using
                            Siesta keywords as given in the manual. List values
                            are written as fdf blocks with each element on a
                            seperate line, while tuples will write each element
                            in a single line.  ASE units are assumed in the
                            input.
        """
        # Put in the default arguments.
        parameters = self.default_parameters.__class__(**kwargs)

        # Setup the siesta command based on number of nodes.
        if parameters['siesta_executable'] is None:
            siesta = os.environ.get('SIESTA')
            if siesta is None:
                raise ValueError("Either define the 'SIESTA' environment " +
                                 "variable give the 'command' keyword on " +
                                 "initialization.")
        else:
            siesta = parameters['siesta_executable']

        label = parameters['label']
        self.label = label
        n_nodes = parameters['n_nodes']

        if n_nodes > 1:
            command = 'mpirun -np %d %s < ./%s.fdf > ./%s.out'
            command = command % (n_nodes, siesta, label, label)
        else:
            command = '%s < ./%s.fdf > ./%s.out' % (siesta, label, label)

        # Call the base class.
        FileIOCalculator.__init__(
            self,
            command=command,
            **parameters)

    def __getitem__(self, key):
        """Convenience method to retrieve a parameter as
        calculator[key] rather than calculator.parameters[key]

            Parameters:
                -key       : str, the name of the parameters to get.
        """
        return self.parameters[key]

    def species(self, atoms):
        """Find all relevant species depending on the atoms object and
        species input.

            Parameters :
                - atoms : An Atoms object.
        """
        # For each element use default specie from the species input, or set
        # up a default species  from the general default parameters.
        symbols = np.array(atoms.get_chemical_symbols())
        tags = atoms.get_tags()
        species = list(self['species'])
        default_species = [
            s for s in species
            if (s['tag'] is None) and s['symbol'] in symbols]
        default_symbols = [s['symbol'] for s in default_species]
        for symbol in symbols:
            if symbol not in default_symbols:
                specie = Specie(
                    symbol=symbol,
                    basis_set=self['basis_set'],
                    tag=None)
                default_species.append(specie)
                default_symbols.append(symbol)
        assert len(default_species) == len(np.unique(symbols))

        # Set default species as the first species.
        species_numbers = np.zeros(len(atoms), int)
        i = 1
        for specie in default_species:
            mask = symbols == specie['symbol']
            species_numbers[mask] = i
            i += 1

        # Set up the non-default species.
        non_default_species = [s for s in species if not s['tag'] is None]
        for specie in non_default_species:
            mask1 = (tags == specie['tag'])
            mask2 = (symbols == specie['symbol'])
            mask = np.logical_and(mask1, mask2)
            if sum(mask) > 0:
                species_numbers[mask] = i
                i += 1
        all_species = default_species + non_default_species

        return all_species, species_numbers

    def set(self, **kwargs):
        """Set all parameters.

            Parameters:
                -kwargs  : Dictionary containing the keywords defined in
                           SiestaParameters.
        """
        # Put in the default arguments.
        kwargs = self.default_parameters.__class__(**kwargs)

        # Check energy inputs.
        for arg in ['mesh_cutoff', 'energy_shift']:
            value = kwargs.get(arg)
            if not (isinstance(value, (float, int)) and value > 0):
                mess = "'%s' must be a positive number(in eV), \
                    got '%s'" % (arg, value)
                raise ValueError(mess)

        # Check the basis set input.
        basis_set = kwargs.get('basis_set')
        allowed = self.allowed_basis_names
        if not (isinstance(basis_set, PAOBasisBlock) or basis_set in allowed):
            mess = "Basis must be either %s, got %s" % (allowed, basis_set)
            raise Exception(mess)

        # Check the spin input.
        spin = kwargs.get('spin')
        if spin is not None and (spin not in self.allowed_spins):
            mess = "Spin must be %s, got %s" % (self.allowed_spins, spin)
            raise Exception(mess)

        # Check the functional input.
        xc = kwargs.get('xc')
        if xc in self.allowed_xc:
            functional = xc
            authors = self.allowed_xc[xc][0]
        else:
            found = False
            for key, value in self.allowed_xc.iteritems():
                if xc in value:
                    found = True
                    functional = key
                    authors = xc
                    break

            if not found:
                raise ValueError("Unrecognized 'xc' keyword: '%s'" % xc)
        kwargs['xc'] = (functional, authors)

        # Check fdf_arguments.
        fdf_arguments = kwargs['fdf_arguments']
        if fdf_arguments is not None:
            # Type checking.
            if not isinstance(fdf_arguments, dict):
                raise TypeError("fdf_arguments must be a dictionary.")

            # Check if keywords are allowed.
            fdf_keys = set(fdf_arguments.keys())
            allowed_keys = set(self.allowed_fdf_keywords)
            if not fdf_keys.issubset(allowed_keys):
                offending_keys = fdf_keys.difference(allowed_keys)
                raise ValueError("The 'fdf_arguments' dictionary " +
                                 "argument does not allow " +
                                 "the keywords: %s" % str(offending_keys))

        FileIOCalculator.set(self, **kwargs)

    def calculate(self,
                  atoms=None,
                  properties=['energy'],
                  system_changes=all_changes):
        """Capture the RuntimeError from FileIOCalculator.calculate
        and add a little debug information from the Siesta output.

        See base FileIocalculator for documentation.
        """

        try:
            FileIOCalculator.calculate(
                self,
                atoms=atoms,
                properties=properties,
                system_changes=system_changes)

        # Here a test to check if the potential are in the right place!!!
        except RuntimeError as e:
            try:
                with open(self.label + '.out', 'r') as f:
                    lines = f.readlines()
                debug_lines = 10
                print('##### %d last lines of the Siesta output' % debug_lines)
                for line in lines[-20:]:
                    print(line.strip())
                print('##### end of siesta output')
                raise e
            except:
                raise e

    def write_input(self, atoms, properties=None, system_changes=None):
        """Write input (fdf)-file.
        See calculator.py for further details.

        Parameters:
            - atoms        : The Atoms object to write.
            - properties   : The properties which should be calculated.
            - system_changes : List of properties changed since last run.
        """
        # Call base calculator.
        FileIOCalculator.write_input(
            self,
            atoms=atoms,
            properties=properties,
            system_changes=system_changes)

        if system_changes is None and properties is None:
            return

        filename = self.label + '.fdf'

        # On any changes, remove all analysis files.
        if system_changes is not None:
            self.remove_analysis()

        # Start writing the file.
        with open(filename, 'w') as f:
            # First write explicitly given options to
            # allow the user to overwrite anything.
            self._write_fdf_arguments(f)

            # Use the saved density matrix if only 'cell' and 'positions'
            # haved changes.
            if (system_changes is None or
                ('numbers' not in system_changes and
                 'initial_magmoms' not in system_changes and
                 'initial_charges' not in system_changes)):
                f.write(format_fdf('DM.UseSaveDM', True))

            # Save density.
            if 'density' in properties:
                f.write(format_fdf('SaveRho', True))

            # Write system name and label.
            f.write(format_fdf('SystemName', self.label))
            f.write(format_fdf('SystemLabel', self.label))

            # Force siesta to return error on no convergence.
            f.write(format_fdf('SCFMustConverge', True))

            # Write the rest.
            self._write_species(f, atoms)
            self._write_kpts(f)
            self._write_structure(f, atoms)

    def read(self, filename):
        """Read parameters from file."""
        if not os.path.exists(filename):
            raise ReadError("The restart file '%s' does not exist" % filename)
        self.atoms = xv_to_atoms(filename)
        self.read_results()

    def _write_fdf_arguments(self, f):
        """Write directly given fdf-arguments.
        """
        fdf_arguments = self.parameters['fdf_arguments']

        # Early return
        if fdf_arguments is None:
            return

        for key, value in fdf_arguments.iteritems():
            if key in self.unit_fdf_keywords.keys():
                value = ('%.8f ' % value, self.unit_fdf_keywords[key])
                f.write(format_fdf(key, value))
            elif key in self.allowed_fdf_keywords:
                f.write(format_fdf(key, value))
            else:
                raise ValueError("%s not in allowed keywords." % key)

    def remove_analysis(self):
        """ Remove all analysis files"""
        filename = self.label + '.RHO'
        if os.path.exists(filename):
            os.remove(filename)

    def _write_structure(self, f, atoms):
        """Translate the Atoms object to fdf-format.

        Parameters:
            - f:     An open file object.
            - atoms: An atoms object.
        """
        unit_cell = atoms.get_cell()
        xyz = atoms.get_positions()
        f.write('\n')
        f.write(format_fdf('NumberOfAtoms', len(xyz)))

        # Write lattice vectors
        default_unit_cell = np.eye(3, dtype=float)
        if np.any(unit_cell != default_unit_cell):
            f.write(format_fdf('LatticeConstant', '1.0 Ang'))
            f.write('%block LatticeVectors\n')
            for i in range(3):
                for j in range(3):
                    s = string.rjust('    %.15f' % unit_cell[i, j], 16) + ' '
                    f.write(s)
                f.write('\n')
            f.write('%endblock LatticeVectors\n')
            f.write('\n')

        self._write_atomic_coordinates(f, atoms)

        # Write magnetic moments.
        magmoms = atoms.get_initial_magnetic_moments()

        # The DM.InitSpin block must be written to initialize to
        # no spin. SIESTA default is FM initialization, if the
        # block is not written, but  we must conform to the
        # atoms object.
        if self['spin'] != 'UNPOLARIZED':
            f.write('%block DM.InitSpin\n')
            for n, M in enumerate(magmoms):
                if M != 0:
                    f.write('    %d %.14f\n' % (n + 1, M))
            f.write('%endblock DM.InitSpin\n')
            f.write('\n')

    def _write_atomic_coordinates(self, f, atoms):
        """Write atomic coordinates.

        Parameters:
            - f:     An open file object.
            - atoms: An atoms object.
        """
        species, species_numbers = self.species(atoms)
        f.write('\n')
        f.write('AtomicCoordinatesFormat  Ang\n')
        f.write('%block AtomicCoordinatesAndAtomicSpecies\n')
        for atom, number in zip(atoms, species_numbers):
            xyz = atom.position
            line = string.rjust('    %.9f' % xyz[0], 16) + ' '
            line += string.rjust('    %.9f' % xyz[1], 16) + ' '
            line += string.rjust('    %.9f' % xyz[2], 16) + ' '
            line += str(number) + '\n'
            f.write(line)
        f.write('%endblock AtomicCoordinatesAndAtomicSpecies\n')
        f.write('\n')

        origin = tuple(-atoms.get_celldisp().flatten())
        f.write('%block AtomicCoordinatesOrigin\n')
        f.write('     %.4f  %.4f  %.4f\n' % origin)
        f.write('%endblock AtomicCoordinatesOrigin\n')
        f.write('\n')

    def _write_kpts(self, f):
        """Write kpts.

        Parameters:
            - f : Open filename.
        """
        kpts = np.array(self['kpts'])
        f.write('\n')
        f.write('#KPoint grid\n')
        f.write('%block kgrid_Monkhorst_Pack\n')

        for i in range(3):
            s = ''
            if i < len(kpts):
                number = kpts[i]
                displace = 0.0
            else:
                number = 1
                displace = 0
            for j in range(3):
                if j == i:
                    write_this = number
                else:
                    write_this = 0
                s += '     %d  ' % write_this
            s += '%1.1f\n' % displace
            f.write(s)
        f.write('%endblock kgrid_Monkhorst_Pack\n')
        f.write('\n')

    def _write_species(self, f, atoms):
        """Write input related the different species.

        Parameters:
            - f:     An open file object.
            - atoms: An atoms object.
        """
        energy_shift = '%.4f eV' % self['energy_shift']
        f.write('\n')
        f.write(format_fdf('PAO_EnergyShift', energy_shift))
        mesh_cutoff = '%.4f eV' % self['mesh_cutoff']
        f.write(format_fdf('MeshCutoff', mesh_cutoff))

        species, species_numbers = self.species(atoms)
        if self['spin'] == 'UNPOLARIZED':
            f.write(format_fdf('SpinPolarized', False))
        elif self['spin'] == 'COLLINEAR':
            f.write(format_fdf('SpinPolarized', True))
        elif self['spin'] == 'FULL':
            f.write(format_fdf('SpinPolarized', True))
            f.write(format_fdf('NonCollinearSpin', True))

        functional, authors = self.parameters['xc']
        f.write('\n')
        f.write(format_fdf('XC_functional', functional))
        if authors is not None:
            f.write(format_fdf('XC_authors', authors))
        f.write('\n')

        if not self['pseudo_path'] is None:
            pseudo_path = self['pseudo_path']
        elif 'SIESTA_PP_PATH' in os.environ:
            pseudo_path = os.environ['SIESTA_PP_PATH']
        else:
            mess = "Please set the environment variable 'SIESTA_PP_PATH'"
            raise Exception(mess)

        f.write(format_fdf('NumberOfSpecies', len(species)))

        pao_basis = []
        chemical_labels = []
        basis_sizes = []
        for species_number, specie in enumerate(species):
            species_number += 1
            symbol = specie['symbol']
            atomic_number = atomic_numbers[symbol]

            if specie['pseudopotential'] is None:
                if self.pseudo_qualifier() == '':
                    label = symbol
                    pseudopotential = label + '.psf'
                else:
                    label = '.'.join([symbol, self.pseudo_qualifier()])
                    pseudopotential = label + '.psf'
            else:
                pseudopotential = specie['pseudopotential']
                label = os.path.basename(pseudopotential)
                label = '.'.join(label.split('.')[:-1])

            if not os.path.isabs(pseudopotential):
                pseudopotential = join(pseudo_path, pseudopotential)

            if not os.path.exists(pseudopotential):
                mess = "Pseudopotential '%s' not found" % pseudopotential
                raise RuntimeError(mess)

            name = os.path.basename(pseudopotential)
            name = name.split('.')
            name.insert(-1, str(species_number))
            if specie['ghost']:
                name.insert(-1, 'ghost')
                atomic_number = -atomic_number
            name = '.'.join(name)

            if join(os.getcwd(), name) != pseudopotential:
                if islink(name) or isfile(name):
                    os.remove(name)
                os.symlink(pseudopotential, name)

            label = '.'.join(np.array(name.split('.'))[:-1])
            string = '    %d %d %s' % (species_number, atomic_number, label)
            chemical_labels.append(string)
            if isinstance(specie['basis_set'], PAOBasisBlock):
                pao_basis.append(specie['basis_set'].script(label))
            else:
                basis_sizes.append((label, specie['basis_set']))
        f.write((format_fdf('ChemicalSpecieslabel', chemical_labels)))
        f.write('\n')
        f.write((format_fdf('PAO.Basis', pao_basis)))
        f.write((format_fdf('PAO.BasisSizes', basis_sizes)))
        f.write('\n')

    def pseudo_qualifier(self):
        """Get the extra string used in the middle of the pseudopotential.
        The retrieved pseudopotential for a specific element will be
        'H.xxx.psf' for the element 'H' with qualifier 'xxx'. If qualifier
        is set to None then the qualifier is set to functional name.
        """
        if self['pseudo_qualifier'] is None:
            return self['xc'][0].lower()
        else:
            return self['pseudo_qualifier']

    def read_results(self):
        """Read the results.
        """
        self.read_energy()
        self.read_forces_stress()
        self.read_eigenvalues()
        self.read_dipole()
        self.read_pseudo_density()

    def read_pseudo_density(self):
        """Read the density if it is there.
        """
        filename = self.label + '.RHO'
        if isfile(filename):
            self.results['density'] = read_rho(filename)

    def read_energy(self):
        """Read energy from SIESTA's text-output file.
        """
        with open(self.label + '.out', 'r') as f:
            text = f.read().lower()

        assert 'error' not in text
        lines = iter(text.split('\n'))

        # Get the number of grid points used:
        for line in lines:
            if line.startswith('initmesh: mesh ='):
                n_points = [int(word) for word in line.split()[3:8:2]]
                self.results['n_grid_point'] = n_points
                break

        for line in lines:
            if line.startswith('siesta: etot    ='):
                self.results['energy'] = float(line.split()[-1])
                line = lines.next()
                self.results['free_energy'] = float(line.split()[-1])
                break
        else:
            raise RuntimeError

    def read_forces_stress(self):
        """Read the forces and stress from the FORCE_STRESS file.
        """
        with open('FORCE_STRESS', 'r') as f:
            lines = f.readlines()

        stress_lines = lines[1:4]
        stress = np.empty((3, 3))
        for i in range(3):
            line = stress_lines[i].strip().split(' ')
            line = [s for s in line if len(s) > 0]
            stress[i] = map(float, line)

        self.results['stress'] = np.array(
            [stress[0, 0], stress[1, 1], stress[2, 2],
             stress[1, 2], stress[0, 2], stress[0, 1]])

        start = 5
        self.results['forces'] = np.zeros((len(lines) - start, 3), float)
        for i in range(start, len(lines)):
            line = [s for s in lines[i].strip().split(' ') if len(s) > 0]
            self.results['forces'][i - start] = map(float, line[2:5])

    def read_eigenvalues(self):
        """Read eigenvalues from the '.EIG' file.
        This is done pr. kpoint.
        """
        assert os.access(self.label + '.EIG', os.F_OK)
        assert os.access(self.label + '.KP', os.F_OK)

        # Read k point weights
        text = open(self.label + '.KP', 'r').read()
        lines = text.split('\n')
        n_kpts = int(lines[0].strip())
        self.weights = np.zeros((n_kpts,))
        for i in range(n_kpts):
            l = lines[i + 1].split()
            self.weights[i] = float(l[4])

        # Read eigenvalues and fermi-level
        with open(self.label + '.EIG', 'r') as f:
            text = f.read()
        lines = text.split('\n')
        e_fermi = float(lines[0].split()[0])
        tmp = lines[1].split()
        self.n_bands = int(tmp[0])
        n_spin_bands = int(tmp[1])
        self.spin_pol = n_spin_bands == 2
        lines = lines[2:-1]
        lines_per_kpt = (self.n_bands * n_spin_bands / 10 +
                         int((self.n_bands * n_spin_bands) % 10 != 0))
        eig = dict()
        for i in range(len(self.weights)):
            tmp = lines[i * lines_per_kpt:(i + 1) * lines_per_kpt]
            v = [float(v) for v in tmp[0].split()[1:]]
            for l in tmp[1:]:
                v.extend([float(t) for t in l.split()])
            if self.spin_pol:
                eig[(i, 0)] = np.array(v[0:self.n_bands])
                eig[(i, 1)] = np.array(v[self.n_bands:])
            else:
                eig[(i, 0)] = np.array(v)

        self.results['fermi_energy'] = e_fermi
        self.results['eigenvalues'] = eig

    def read_dipole(self):
        """Read dipole moment.
        """
        dipole = np.zeros([1, 3])
        with open(self.label + '.out', 'r') as f:
            for line in f:
                if line.rfind('Electric dipole (Debye)') > -1:
                    dipole = np.array([float(f) for f in line.split()[5:8]])

        # debye to e*Ang
        self.results['dipole'] = dipole * 0.2081943482534