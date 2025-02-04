import os
import shutil
import subprocess
import warnings
from glob import glob
from os import path
from os.path import splitext, isfile, join, isdir, basename, abspath
from typing import Optional

import mrcfile

from tomotools.utils import mdocfile, util


def assert_subframes_list(subframes: list, is_split):
    if not all(isinstance(subframe, SubFrame) for subframe in subframes):
        raise ValueError('Only a list of SubFrames is supported!')
    for subframe in subframes:
        subframe.assert_files_exist(is_split)


def sanitize_subframes_list(subframes: list):
    if not all(isinstance(subframe, SubFrame) for subframe in subframes):
        raise ValueError('Only a list of SubFrames is supported!')
    sanitized_list = list()
    for subframe in subframes:
        base_name, ext = splitext(basename(subframe.path))
        if not (base_name.endswith('_EVN') or base_name.endswith('_ODD')):
            sanitized_list.append(subframe)
    return sanitized_list


def sort_subframes_list(subframes: list):
    '''Sorts a list of SubFrames by tilt-angle
    Requires that all SubFrames have a corresponding MDOC file'''
    # Check if the list is a list of subframes
    if not all(isinstance(subframe, SubFrame) for subframe in subframes):
        raise ValueError('sort_subframes_list only supports lists of SubFrames')
    
    return sorted(subframes, key=lambda subframe: subframe.tilt_angle)

def frames2stack(subframes: list, stack_path, full_mdoc: Optional[dict]=None, overwrite_titles=None, skip_evnodd=False):
    # Check if frames and their respective mdoc files exist
    assert_subframes_list(subframes, is_split=False)

    # If all subframes have their own associated mdoc, merge the mdoc files (except titles, see below)
    if all(subframe.subframe_mdoc for subframe in subframes):
        stack_mdoc = {'titles': list(), 'sections': list(), 'framesets': list()}
        for subframe in subframes:
            # Update titles and append frameset as new section
            mdoc = subframe.mdoc
            stack_mdoc['titles'] = mdoc['titles']
            stack_mdoc['sections'].append(mdoc['framesets'][0])
            # Copy global vars (overwrite existing, so again only the last values are kept)
            for key, value in mdoc.items():
                if key not in ('framesets', 'titles', 'sections'):
                    stack_mdoc[key] = value
    
        # Merging the titles is too difficult, I'll just keep the title of the last frame
        if overwrite_titles is not None:
            stack_mdoc['titles'] = overwrite_titles
            
    # Else, just use the input mdoc file which has possibly been reordered
    # TODO: Implement pixel size change if binned
    else:
        stack_mdoc = full_mdoc
    
    # Build pair(s) of output stack and list of movie sums
    full_stack_basename, full_stack_ext = splitext(stack_path)
    stack_subframes_pairs = [(stack_path, [subframe.path for subframe in subframes])]
    
    if not skip_evnodd and all(subframe.is_split for subframe in subframes):
        stack_subframes_pairs += [
            (f'{full_stack_basename}_EVN{full_stack_ext}', [subframe.path_evn for subframe in subframes]),
            (f'{full_stack_basename}_ODD{full_stack_ext}', [subframe.path_odd for subframe in subframes])
        ]

    # Run newstack for the full stack and, if desired, the EVN/ODD halves
    # Run newstack for the full stack and, if desired, the EVN/ODD halves
    for partial_stack_path, partial_stack_subframes in stack_subframes_pairs:
        subprocess.run(['newstack'] + partial_stack_subframes + [partial_stack_path] + ['-quiet'])
        # Update the header of the stack MRC
        with mrcfile.mmap(partial_stack_path, 'r+') as mrc:
            # Copy the first 10 titles into the newly created mrc
            mrc.update_header_from_data()
            mrc.update_header_stats()
            for i in range(10):
                title = stack_mdoc['titles'][i].encode() if i < len(stack_mdoc['titles']) else b''
                mrc.header['label'][i] = title
            mrc.header['nlabl'] = len(stack_mdoc['titles'])
            mrc.voxel_size = stack_mdoc['sections'][0]['PixelSpacing']
            # Copy over some global information from the first section into the mdoc
            stack_mdoc['PixelSpacing'] = stack_mdoc['sections'][0]['PixelSpacing']
            stack_mdoc['ImageFile'] = basename(partial_stack_path)
            stack_mdoc['ImageSize'] = [mrc.header['nx'].item(), mrc.header['ny'].item()]
            stack_mdoc['DataMode'] = mrc.header['mode'].item()
            # Update the header of the stack MRC
            with mrcfile.mmap(partial_stack_path, 'r+') as mrc:
                # Copy the first 10 titles into the newly created mrc
                mrc.update_header_from_data()
                mrc.update_header_stats()
                for i in range(10):
                    title = stack_mdoc['titles'][i].encode() if i < len(stack_mdoc['titles']) else b''
                    mrc.header['label'][i] = title
                mrc.header['nlabl'] = len(stack_mdoc['titles'])
                mrc.voxel_size = stack_mdoc['sections'][0]['PixelSpacing']
                # Copy over some global information from the first section into the mdoc
                stack_mdoc['PixelSpacing'] = stack_mdoc['sections'][0]['PixelSpacing']
                stack_mdoc['ImageFile'] = basename(partial_stack_path)
                stack_mdoc['ImageSize'] = [mrc.header['nx'].item(), mrc.header['ny'].item()]
                stack_mdoc['DataMode'] = mrc.header['mode'].item()
    mdocfile.write(stack_mdoc, f'{stack_path}.mdoc')
    
    return stack_path, stack_mdoc


class SubFrame:
    @property
    def mdoc_path(self):
        return f'{self.path}.mdoc'

    @property
    def mdoc(self):
        if self._mdoc is None and self.subframe_mdoc:
            self._mdoc = mdocfile.read(self.mdoc_path)
        return self._mdoc

    @property
    def path_evn(self):
        base, ext = splitext(self.path)
        return f'{base}_EVN{ext}'

    @property
    def path_odd(self):
        base, ext = splitext(self.path)
        return f'{base}_ODD{ext}'

    @property
    def is_split(self):
        return isfile(self.path_evn) and isfile(self.path_odd)
    
    @property
    def is_mrc(self):
        return self.path.endswith('.mrc') or self.path.endswith('.mrcs')
        
    @property
    def subframe_mdoc(self):
        return isfile(self.mdoc_path)

    def __init__(self, path: str, tilt_angle):
        self.path = path
        self.tilt_angle = float(tilt_angle)
        # MDOC files are read lazily
        self._mdoc = None

    def files_exist(self, is_split) -> bool:
        return self.path is not None and isfile(self.path) and is_split == self.is_split

    def assert_files_exist(self, is_split):
        for file in [self.path] + ([self.path_evn, self.path_odd] if is_split else []):
            if not isfile(file):
                raise FileNotFoundError(f'File does not exist: {file}')


def motioncor2(subframes: list, output_dir: str, splitsum: bool = False, binning: int = 2, group: int = 1, mcrot: int = 0, mcflip: int = 0,
               override_gainref: str = None, gpus: Optional[str]=None):
    assert_subframes_list(subframes, is_split=False)
    gain_ref_dm4 = None
    gain_ref_mrc = None
    mc2_exe = motioncor2_executable()
    if mc2_exe is None:
        raise FileNotFoundError('The MotionCor2 executable could not be found. '
                                'Either specify it by setting MOTIONCOR2_EXECUTABLE '
                                'or put it into the PATH and rename it to "motioncor2"')
    tempdir = join(output_dir, 'motioncor2_temp')
    if not isdir(tempdir):
        os.makedirs(tempdir)

    # If override_gainref is given, check if it is already mrc or needs to be converted. 
    # If neither are given, skip gain correction
    if override_gainref is not None:
        if splitext(override_gainref)[1] == '.dm4':
            gain_ref_dm4 = override_gainref
        elif splitext(override_gainref)[1] == '.mrc':
            gain_ref_mrc = override_gainref
    elif subframes[0].subframe_mdoc:
        # Check, if Subframe mdocs are given, if yes check whether zero or one unique gain refs are given
        gain_refs = set([subframe.mdoc['framesets'][0].get('GainReference', None) for subframe in subframes])
        if len(gain_refs) != 1:
            raise Exception(
                f'Only zero or one unique gain refs are supported, yet {len(gain_refs)} were found in the MDOC files:\n{", ".join(gain_refs)}')
        # The gain ref should be in the same folder as the input file(s), so check if it's there
        gain_ref_dm4 = gain_refs.pop()

    if gain_ref_dm4 is not None:
        if not isfile(gain_ref_dm4):
            raise FileNotFoundError(f'Expected gain reference at {gain_ref_dm4}, aborting')
        print(f'Found unique gain reference {gain_ref_dm4}, converting to MRC')
        # The gain ref is saved in dm4 format, convert to MRC for motioncor
        gain_ref_mrc = splitext(basename(gain_ref_dm4))[0]  # Basename of gain ref without extension and path
        gain_ref_mrc = join(tempdir, gain_ref_mrc) + '.mrc'
        subprocess.run(['dm2mrc', gain_ref_dm4, gain_ref_mrc])

    if gain_ref_mrc is not None:
        if not isfile(gain_ref_mrc):
            raise FileNotFoundError(f"The GainRef file {gain_ref_mrc} doesn't exist, something must have gone wrong!")
        print(f'Using gainref file {gain_ref_mrc}')
    else:
        print(
            'No gain reference is specified in the MDOC files or given as an argument, continuing without gain correction')

    # Link the input files to the working dir
    # so that files that should not be motioncor'ed are not
    # TODO: switch off sharpening over frames (Bft?) or switch to imod alignframes
    for subframe in subframes:
        os.symlink(abspath(subframe.path), join(tempdir, basename(subframe.path)))

        command = [mc2_exe,
                   '-OutMrc', abspath(output_dir) + path.sep,
                      '-Patch', '7', '5',
                      '-Iter', '10',
                      '-Tol', '0.5',
                      '-Kv', '300',
                      '-FtBin', str(binning),
                      '-Group', str(group),
                      '-Serial', '1']
        
        if subframe.is_mrc:
            command += ['-InMrc', abspath(tempdir) + path.sep]
        else:
            command += ['-InTiff', abspath(tempdir) + path.sep]
        
    if gpus is None:
        num_gpus = int(util.gpuinfo()['Attached GPUs'])
        command += ['-Gpu'] + [str(i) for i in range(num_gpus)] if num_gpus > 0 else []
    else:
        command += ['-Gpu', gpus]
        
    if splitsum:
        command += ['-SplitSum', '1']
    if gain_ref_mrc is not None:
        command += ['-Gain', abspath(gain_ref_mrc),
                    '-RotGain', str(mcrot), 
                    '-FlipGain', str(mcflip)]

    if gain_ref_dm4 is not None and check_defects(gain_ref_dm4) is not None:
        command += ['-DefectMap', defects_tif(gain_ref_dm4, tempdir, subframes[0].path)]
        
    with open(join(output_dir, 'motioncor2.log'), 'a') as out, open(join(output_dir, 'motioncor2.err'), 'a') as err:
        subprocess.run(command, cwd=tempdir, stdout=out, stderr=err)
    
    # If present, copy the mdoc files to the output dir, rename from .tif.mdoc to .mrc.mdoc
    # they are read and then written and not just copied so that the GainReference field can be removed
    # and the pixel spacing can be adjusted
    
    for subframe in subframes:
        if subframe.subframe_mdoc:
            # Sanity check: there should be only one frameset
            if not (isinstance(subframe.mdoc['framesets'], list) and len(subframe.mdoc['framesets']) == 1):
                raise 'Unexpected MDOC format: tomotools can only handle a single frameset per mdoc'
            subframe.mdoc['framesets'][0]['PixelSpacing'] *= binning
            subframe.mdoc['framesets'][0]['Binning'] *= binning
            if 'GainReference' in subframe.mdoc['framesets'][0]:
                del subframe.mdoc['framesets'][0]['GainReference']
            mdocfile.write(subframe.mdoc,
                           join(output_dir, splitext(splitext(basename(subframe.mdoc_path))[0])[0] + '.mrc.mdoc'))
    
    shutil.rmtree(tempdir)


    # Build a list of output files that will be returned to the caller
    output_frames = [SubFrame(path=join(output_dir, splitext(basename(subframe.path))[0] + '.mrc'), tilt_angle=subframe.tilt_angle) for subframe in
                     subframes]
    print('Checking MotionCor2 output files')
    assert_subframes_list(output_frames, is_split=splitsum)
    return output_frames

def motioncor2_executable() -> Optional[str]:
    '''The MotionCor executable can be set with one of the following ways (in order of priority):
    1. Setting the MOTIONCOR2_EXECUTABLE variable to the full path of the executable file
    2. Putting the appropriate executable into the PATH and renaming it to "motioncor2"'''
    if 'MOTIONCOR2_EXECUTABLE' in os.environ:
        mc2_exe = os.environ['MOTIONCOR2_EXECUTABLE']
        if isfile(mc2_exe):
            return mc2_exe
        else:
            warnings.warn(f'MOTIONCOR2_EXECUTABLE is set to "{mc2_exe}", but the file does not exist. Falling back to PATH')
    return shutil.which('motioncor2')

def aretomo_executable() -> Optional[str]:
    '''The AreTomo executable can be set with one of the following ways (in order of priority):
    1. Setting the ARETOMO_EXECUTABLE variable to the full path of the executable file
    2. Putting the appropriate executable into the PATH and renaming it to "aretomo"'''
    if 'ARETOMO_EXECUTABLE' in os.environ:
        aretomo_exe = os.environ['ARETOMO_EXECUTABLE']
        if isfile(aretomo_exe):
            return aretomo_exe
        else:
            warnings.warn(f'ARETOMO_EXECUTABLE is set to "{aretomo_exe}", but the file does not exist. Falling back to PATH')
    return shutil.which('AreTomo')

def sem2mc2(RotationAndFlip: int = 0):
    ''' Converts SerialEM property RotationAndFlip into MotionCor2-compatibly -RotGain / -FlipGain values.
    Using List on https://bio3d.colorado.edu/SerialEM/hlp/html/setting_up_serialem.htm#cameraOrientation as Reference, 
    For MotionCor2: Rotation = n*90deg, Flip 1 = flip around X, Flip 2 = flip around Y
    Returns a List with first item as rotation and second item as flip.'''
    conv = {0: [0,0], 1: [3,0], 2: [2,0], 3: [1, 0], 4: [0,2], 5: [1,2], 6: [2,2], 7: [3,2] }
    return conv[RotationAndFlip]


def check_defects(gainref: os.PathLike):
    ''' Checks for a SerialEM-created defects file and -if found- returns file name. '''
    defects_temp = list()

    defects_temp.extend(glob(path.join(path.dirname(gainref), 'defects*.txt')))

    if len(defects_temp) == 1:
        return defects_temp[0]

    elif len(defects_temp) > 1:
        print('Multiple defect files are found. Skipping defects correction.')
        return None
    
    else:
        return None
    
def defects_tif(gainref,tempdir,template):
    ''' Creates a -DefectsMap input for MotionCor2 from SerialEM defects txt in the passed temporary directory.
    Requires a template file with the dimensions of the frames to be corrected. '''
    
    defects_txt = check_defects(gainref)
    defects_tif = join(tempdir,f'{basename(defects_txt)}.tif')
    
    subprocess.run(['clip', 'defect', '-D', defects_txt, template, defects_tif])
    print(f'Found and converted defects file {defects_tif}')
    return defects_tif