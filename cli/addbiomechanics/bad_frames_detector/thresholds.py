import nimblephysics as nimble
from typing import List, Tuple, Optional, Dict
from addbiomechanics.bad_frames_detector.abstract_detector import AbstractDetector
import json
import numpy as np
import os
import importlib.resources


class ThresholdsDetector(AbstractDetector):
    def __init__(self):
        super().__init__()
        # Access a static file
        self.foot_marker_data = {
            "L_HEEL_1": {
                "mesh_patterns": ["l_foot"],
                "offset": [-0.01, 0.0, -0.02]
            },
            "L_HEEL_2": {
                "mesh_patterns": ["l_foot"],
                "offset": [-0.01, 0.0, 0.035]
            },
            "L_TOES_1": {
                "mesh_patterns": ["l_bofoot"],
                "offset": [0.1, 0.0, 0.03]
            },
            "L_TOES_2": {
                "mesh_patterns": ["l_bofoot"],
                "offset": [0.07, 0.0, -0.08]
            },
            "L_INNER_FOOT": {
                "mesh_patterns": ["l_bofoot"],
                "offset": [0.0, 0.0, 0.065]
            },
            "R_HEEL_1": {
                "mesh_patterns": ["r_foot"],
                "offset": [-0.01, 0.0, -0.02]
            },
            "R_HEEL_2": {
                "mesh_patterns": ["r_foot"],
                "offset": [-0.01, 0.0, 0.035]
            },
            "R_TOES_1": {
                "mesh_patterns": ["r_bofoot"],
                "offset": [0.1, 0.0, -0.03]
            },
            "R_TOES_2": {
                "mesh_patterns": ["r_bofoot"],
                "offset": [0.07, 0.0, 0.08]
            },
            "R_INNER_FOOT": {
                "mesh_patterns": ["r_bofoot"],
                "offset": [0.0, 0.0, -0.065]
            }
        }

    @staticmethod
    def has_input_outliers(trial_header: nimble.biomechanics.SubjectOnDiskTrial,
                           raw_force_plate_forces: List[List[np.ndarray]]) -> bool:
        """
        Check if the raw sensor input data has outliers, which would produce bad fits during the optimization steps.
        :param trial_header:
        :return:
        """
        marker_observations: List[Dict[str, np.ndarray]] = trial_header.getMarkerObservations()

        for t in range(len(marker_observations)):

            # 1. Check for any marker that is too far from the median of the marker cloud

            # 1.1. Collect the median of the marker cloud
            marker_xs: List[float] = []
            marker_ys: List[float] = []
            marker_zs: List[float] = []
            for marker_name in marker_observations[t]:
                marker_pos = marker_observations[t][marker_name]
                marker_xs.append(marker_pos[0])
                marker_ys.append(marker_pos[1])
                marker_zs.append(marker_pos[2])
            median_x = np.median(marker_xs)
            median_y = np.median(marker_ys)
            median_z = np.median(marker_zs)

            # 1.2. Check the distance of each marker from the median
            any_out_of_bounds = False
            for marker_name in marker_observations[t]:
                marker_pos = marker_observations[t][marker_name]
                if np.linalg.norm(marker_pos - np.array([median_x, median_y, median_z])) > 2.5:
                    any_out_of_bounds = True
                    break

            if any_out_of_bounds:
                return True

            # 2. Check for any force plate that has a total force magnitude greater than 2500 N

            for plate in range(len(raw_force_plate_forces)):
                force = raw_force_plate_forces[plate][t]
                if np.linalg.norm(force) > 2500.0:
                    return True

    @staticmethod
    def smooth_positions(dt: float, frames: nimble.biomechanics.FrameList) -> Tuple[np.ndarray, np.ndarray]:
        """
        Smooth the positions and velocities of the frames using an acceleration minimizing smoother.

        :param dt:
        :param frames:
        :return:
        """
        num_dofs = frames[0].processingPasses[0].pos.shape[0]
        trial_len = len(frames)

        poses = np.zeros((num_dofs, trial_len))
        for t in range(trial_len):
            frame = frames[t]
            poses[:, t] = frame.processingPasses[0].pos

        acc_weight = 1.0 / (dt * dt)
        regularization_weight = 1000.0
        acc_minimizer = nimble.utils.AccelerationMinimizer(trial_len, acc_weight, regularization_weight,
                                                           numIterations=500)

        lowpass_poses = np.zeros((num_dofs, trial_len))
        for i in range(poses.shape[0]):
            lowpass_poses[i, :] = acc_minimizer.minimize(poses[i, :])
        poses = lowpass_poses

        vels = np.zeros((num_dofs, trial_len))
        for t in range(1, trial_len):
            vels[:, t] = (poses[:, t] - poses[:, t - 1]) / dt
        vels[:, 0] = vels[:, 1]

        return poses, vels

    def get_foot_marker_sets(self, osim: nimble.biomechanics.OpenSimFile) -> List[
        List[Tuple[nimble.dynamics.BodyNode, np.ndarray]]]:
        """
        Get the foot marker sets for the left and right feet, based on the configuration JSON.

        :param osim:
        :return:
        """
        left_foot_markers = []
        right_foot_markers = []
        for marker_name in self.foot_marker_data:
            marker = self.foot_marker_data[marker_name]
            mesh_patterns: List[str] = marker['mesh_patterns']
            mesh_name: Optional[str] = None
            for mesh in osim.meshMap:
                if any([pattern in mesh for pattern in mesh_patterns]):
                    mesh_name = mesh
                    break
            if mesh_name is None:
                print(f"Could not find mesh for marker {marker_name}")
                continue
            offset: np.ndarray = np.array(marker['offset'])
            body_name: str = osim.meshMap[mesh_name][0]
            relative_t: nimble.math.Isometry3 = osim.meshMap[mesh_name][1]
            body: nimble.dynamics.BodyNode = osim.skeleton.getBodyNode(body_name)
            body_offset: np.ndarray = relative_t.multiply(offset)
            if marker_name[0] == 'L':
                left_foot_markers.append((body, body_offset))
            elif marker_name[0] == 'R':
                right_foot_markers.append((body, body_offset))
        return [left_foot_markers, right_foot_markers]

    @staticmethod
    def get_force_weighted_convex_foot_cop_error(skel: nimble.dynamics.Skeleton,
                                                 foot_markers: List[List[Tuple[nimble.dynamics.BodyNode, np.ndarray]]],
                                                 positions: np.ndarray,
                                                 raw_force_plate_forces: List[List[np.ndarray]],
                                                 raw_force_plate_cops: List[List[np.ndarray]]) -> float:
        """
        Get the force-weighted convex foot CoP error for the given skeleton, foot markers, positions, and frames.

        :param skel:
        :param foot_markers:
        :param positions:
        :param frames:
        :return:
        """
        num_contact_bodies = len(foot_markers)
        num_force_plates = len(raw_force_plate_forces)
        last_in_contact = [False for _ in range(num_force_plates)]

        contact_distances = []
        contact_forces = []
        for _ in range(num_force_plates):
            contact_distances.append([0.0 for _ in range(num_contact_bodies)])
            contact_forces.append(0.0)

        largest_min_weighted_distance = 0.0
        total_force = 0.0

        for t in range(len(raw_force_plate_forces[0])):
            skel.setPositions(positions[:, t])

            forces = [raw_force_plate_forces[f][t] for f in range(num_force_plates)]
            cops = [raw_force_plate_cops[f][t] for f in range(num_force_plates)]
            for f in range(len(forces)):
                force = forces[f]
                force_mag = np.linalg.norm(force)
                if force_mag > 10.0:
                    cop = cops[f]
                    if not last_in_contact[f]:
                        last_in_contact[f] = True
                    for b in range(num_contact_bodies):
                        marker_positions = skel.getMarkerWorldPositions(foot_markers[b])
                        marker_positions_as_3vecs = []
                        for i in range(int(len(marker_positions) / 3)):
                            marker_positions_as_3vecs.append(marker_positions[i * 3:i * 3 + 3])
                        dist = nimble.math.distancePointToConvexHullProjectedTo2D(cop, marker_positions_as_3vecs,
                                                                                  [0.0, 1.0, 0.0])
                        contact_distances[f][b] += dist * force_mag
                        contact_forces[f] += force_mag
                else:
                    if last_in_contact[f]:
                        if contact_forces[f] > 100.0:
                            weighted_average_distances = [contact_distances[f][body] / contact_forces[f] for body in
                                                          range(num_contact_bodies)]
                            min_weighted_distance = min(weighted_average_distances)
                            if min_weighted_distance > largest_min_weighted_distance:
                                largest_min_weighted_distance = min_weighted_distance
                            total_force += contact_forces[f]

                        last_in_contact[f] = False
                        contact_distances[f] = [0.0 for _ in range(num_contact_bodies)]
                        contact_forces[f] = 0.0
                    last_in_contact[f] = False
        for f in range(num_force_plates):
            if last_in_contact[f]:
                if contact_forces[f] > 100.0:
                    weighted_average_distances = [contact_distances[f][body] / contact_forces[f] for body in
                                                  range(num_contact_bodies)]
                    min_weighted_distance = min(weighted_average_distances)
                    if min_weighted_distance > largest_min_weighted_distance:
                        largest_min_weighted_distance = min_weighted_distance
                    total_force += contact_forces[f]
                last_in_contact[f] = False
                contact_distances[f] = [0.0 for _ in range(num_contact_bodies)]
                contact_forces[f] = 0.0

        return largest_min_weighted_distance

    @staticmethod
    def get_num_steps(raw_force_plate_forces: List[List[np.ndarray]],
                      raw_force_plate_cops: List[List[np.ndarray]]) -> Tuple[int, List[int]]:
        num_force_plates = len(raw_force_plate_forces)
        trial_len = len(raw_force_plate_forces[0])
        num_steps = 0
        num_steps_per_force_plate = [0 for _ in range(num_force_plates)]
        last_in_contact = [False for _ in range(num_force_plates)]
        for t in range(trial_len):
            forces = [raw_force_plate_forces[f][t] for f in range(num_force_plates)]
            for f in range(len(forces)):
                force = forces[f]
                if np.linalg.norm(force) > 10.0:
                    if not last_in_contact[f]:
                        last_in_contact[f] = True
                else:
                    if last_in_contact[f]:
                        last_in_contact[f] = False
                        num_steps += 1
                        num_steps_per_force_plate[f] += 1
                    last_in_contact[f] = False
        for f in range(num_force_plates):
            if last_in_contact[f]:
                num_steps += 1
                num_steps_per_force_plate[f] += 1
        return num_steps, num_steps_per_force_plate

    @staticmethod
    def get_foot_travel_distance_in_contact(skel: nimble.dynamics.Skeleton,
                                            ground_bodies: List[nimble.dynamics.BodyNode],
                                            positions: np.ndarray,
                                            raw_force_plate_forces: List[List[np.ndarray]],
                                            raw_force_plate_cops: List[List[np.ndarray]]) -> List[float]:
        trial_len = len(raw_force_plate_forces[0])
        num_contact_bodies = len(ground_bodies)
        body_last_in_contact = [False for _ in range(num_contact_bodies)]
        body_started_contact = [np.zeros(3) for _ in range(num_contact_bodies)]
        body_last_position = [np.zeros(3) for _ in range(num_contact_bodies)]
        step_travel_distances = []
        for t in range(trial_len):
            skel.setPositions(positions[:, t])
            ground_body_locations = [body.getWorldTransform().translation() for body in ground_bodies]
            forces = [raw_force_plate_forces[f][t] for f in range(len(raw_force_plate_forces))]
            for f in range(len(ground_body_locations)):
                force = forces[f * 3:f * 3 + 3]
                if np.linalg.norm(force) > 10.0:
                    body_last_position[f] = ground_body_locations[f]
                    if not body_last_in_contact[f]:
                        body_started_contact[f] = ground_body_locations[f]
                        body_last_in_contact[f] = True
                else:
                    if body_last_in_contact[f]:
                        body_last_in_contact[f] = False
                        step_travel_distances.append(np.linalg.norm(body_last_position[f] - body_started_contact[f]))
        for f in range(num_contact_bodies):
            if body_last_in_contact[f]:
                step_travel_distances.append(np.linalg.norm(body_last_position[f] - body_started_contact[f]))
        return step_travel_distances

    @staticmethod
    def get_root_box_volume(positions: np.ndarray):
        # Compute the root box volumes
        root_translation = positions[3:6, :]
        root_box_lower_bound = np.min(root_translation, axis=1)
        root_box_upper_bound = np.max(root_translation, axis=1)
        root_box_volume = np.sum(root_box_upper_bound - root_box_lower_bound)
        return root_box_volume

    def estimate_trial_type(self,
                            skel: nimble.dynamics.Skeleton,
                            foot_bodies: List[nimble.dynamics.BodyNode],
                            positions: np.ndarray,
                            velocities: np.ndarray,
                            raw_force_plate_forces: List[List[np.ndarray]],
                            raw_force_plate_cops: List[List[np.ndarray]]) -> str:
        num_force_plates = len(raw_force_plate_forces)
        num_steps, _ = self.get_num_steps(raw_force_plate_forces, raw_force_plate_cops)
        step_travel_distances = self.get_foot_travel_distance_in_contact(skel, foot_bodies, positions, raw_force_plate_forces, raw_force_plate_cops)
        root_box_volume = self.get_root_box_volume(positions)
        max_root_rot_vel = np.max(np.abs(velocities[0:3, :]))

        if root_box_volume < 0.06 or max_root_rot_vel < 0.1:
            return 'Static'
        if root_box_volume > 0.8:
            return 'Overground'
        if len(step_travel_distances) > 0 and np.max(step_travel_distances) > 0.4 and num_force_plates == 2:
            return 'Treadmill'
        if num_steps > 15 and num_force_plates == 2:
            return 'Treadmill'
        return 'Overground'

    def estimate_missing_grfs(self, subject: nimble.biomechanics.SubjectOnDisk, trials: List[int]) -> List[List[nimble.biomechanics.MissingGRFReason]]:
        osim: nimble.biomechanics.OpenSimFile = subject.readOpenSimFile(processingPass=0, ignoreGeometry=True)
        skel: nimble.dynamics.Skeleton = osim.skeleton
        foot_markers: List[List[Tuple[nimble.dynamics.BodyNode, np.ndarray]]] = self.get_foot_marker_sets(osim)
        foot_bodies = [skel.getBodyNode(body_name) for body_name in subject.getGroundForceBodies()]

        if not subject.hasLoadedAllFrames():
            subject.loadAllFrames(doNotStandardizeForcePlateData=True)
        trial_protos = subject.getHeaderProto().getTrials()

        result: List[List[nimble.biomechanics.MissingGRFReason]] = []
        for trial in trials:
            trial_len = subject.getTrialLength(trial)
            trial_proto = trial_protos[trial]

            raw_force_plates: List[nimble.biomechanics.ForcePlate] = trial_proto.getForcePlates()
            raw_force_plate_forces: List[List[np.ndarray]] = [plate.forces for plate in raw_force_plates]
            raw_force_plate_cops: List[List[np.ndarray]] = [plate.centersOfPressure for plate in raw_force_plates]

            # 1. Rapidly check if the entire trial is bad for some reason that can be checked cheaply, without running
            # the smoother first.
            if np.mean(subject.getTrialMarkerRMSs(trial, 0)) > 0.08:
                result.append([nimble.biomechanics.MissingGRFReason.tooHighMarkerRMS] * trial_len)
                continue
            elif self.has_input_outliers(trial_proto, raw_force_plate_forces):
                result.append([nimble.biomechanics.MissingGRFReason.hasInputOutliers] * trial_len)
                continue
            if len(raw_force_plate_forces) == 0:
                result.append([nimble.biomechanics.MissingGRFReason.hasNoForcePlateData] * trial_len)
                continue

            # 2. Smooth the positions and velocities
            dt = subject.getTrialTimestep(trial)
            poses = trial_proto.getPasses()[1].getPoses()
            vels = trial_proto.getPasses()[1].getVels()

            # 3. Check if the trial has badly wrapped IK based on the smoothed velocities
            if np.max(np.abs(vels)) > 40.0:
                result.append([nimble.biomechanics.MissingGRFReason.velocitiesStillTooHighAfterFiltering] * trial_len)
                continue

            # 4. Check for outlying CoP values
            if self.get_force_weighted_convex_foot_cop_error(skel, foot_markers, poses, raw_force_plate_forces, raw_force_plate_cops) > 0.01:
                result.append([nimble.biomechanics.MissingGRFReason.copOutsideConvexFootError] * trial_len)
                continue

            # 5. Estimate the trial type
            trial_type = self.estimate_trial_type(skel, foot_bodies, poses, vels, raw_force_plate_forces, raw_force_plate_cops)

            # 6. Check for missing GRFs on footsteps off force plates, for data that is overground and has passed all
            # the other checks -- For now we just check if the total force magnitude is less than 10 N.
            if trial_type == 'Overground':
                missing = []
                force_mags: List[float] = []
                for i in range(trial_len):
                    forces = [raw_force_plate_forces[f][i] for f in range(len(raw_force_plate_forces))]
                    total_force_mag = 0.0
                    for force in forces:
                        total_force_mag += np.linalg.norm(force)
                    if total_force_mag < 10.0:
                        missing.append(nimble.biomechanics.MissingGRFReason.zeroForceFrame)
                    else:
                        missing.append(nimble.biomechanics.MissingGRFReason.notMissingGRF)
                    force_mags.append(total_force_mag)

                # Now we can go through and extend all the missing segments into the "rising force ramp" and "falling
                # force ramp" regions at the edge of the missing segments.

                # Start with the rising force ramp, which we can tell by checking left-to-right if any frame has more
                # force magnitude than any of the previous few frames which may have been marked as missing, and if so,
                # extend the missing segment to include that frame.
                extend_frames = 20
                extended_frames = 0

                for i in range(trial_len):
                    if missing[i] != nimble.biomechanics.MissingGRFReason.notMissingGRF or i == 0:
                        for j in range(1, extend_frames + 1):
                            if i + j < trial_len and force_mags[i] < force_mags[i + j]:
                                for k in range(j):
                                    if not missing[i + k]:
                                        extended_frames += 1
                                    missing[i + k] = nimble.biomechanics.MissingGRFReason.extendedToNearestPeakForce
                                # break

                # Now do the same for the falling force ramp, but right-to-left.
                for i in range(trial_len - 1, -1, -1):
                    if missing[i] != nimble.biomechanics.MissingGRFReason.notMissingGRF or i == trial_len - 1:
                        for j in range(1, extend_frames + 1):
                            if i - j >= 0 and force_mags[i] < force_mags[i - j]:
                                for k in range(j):
                                    if not missing[i - k]:
                                        extended_frames += 1
                                    missing[i - k] = nimble.biomechanics.MissingGRFReason.extendedToNearestPeakForce
                                # break

                # print(f"Extended {extended_frames} frames for trial {trial}")
                result.append(missing)
            else:
                result.append([nimble.biomechanics.MissingGRFReason.notMissingGRF] * trial_len)
        return result