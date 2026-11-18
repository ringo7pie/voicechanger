import unittest

from voicechanger import obs_setup


class ObsSetupHelpersTest(unittest.TestCase):
    def test_tracks_body_covers_all_six(self) -> None:
        self.assertEqual(obs_setup._tracks_body((1, 3)),
                         {"1": True, "2": False, "3": True, "4": False, "5": False, "6": False})

    def test_pick_item_by_value_or_name(self) -> None:
        items = [{"itemName": "既定", "itemValue": "default"},
                 {"itemName": "CABLE Output (VB-Audio Virtual Cable)", "itemValue": "{id-cable}"},
                 {"itemName": "マイク (High Definition Audio Device)", "itemValue": "{id-mic}"}]
        self.assertEqual(obs_setup._pick_item(items, name_prefix="cable output")["itemValue"], "{id-cable}")
        self.assertEqual(obs_setup._pick_item(items, value="{id-mic}")["itemName"], "マイク (High Definition Audio Device)")
        self.assertIsNone(obs_setup._pick_item(items, name_prefix="存在しない"))

    def test_recording_settings_match_the_routing_rules(self) -> None:
        parameters = {(category, name): value for category, name, value in obs_setup.PROFILE_PARAMETERS}
        self.assertEqual(parameters[("AdvOut", "RecTracks")], "7", "トラック 1・2・3 を録る")
        self.assertEqual(parameters[("AdvOut", "RecEncoder")], "obs_nvenc_h264_tex", "CPU の x264 にしない")
        self.assertEqual(obs_setup.TRACKS[obs_setup.VOICE_INPUT], (1, 3))
        self.assertEqual(obs_setup.TRACKS[obs_setup.GAME_INPUT], (1, 2))
        self.assertLess(obs_setup.DEFAULT_VOICE_SYNC_OFFSET_MS, 0, "声は遅れるので前へずらす")

    def test_fit_transform_fills_the_canvas(self) -> None:
        transform = obs_setup.fit_transform()
        self.assertEqual(transform["boundsType"], "OBS_BOUNDS_SCALE_INNER")
        self.assertEqual((transform["boundsWidth"], transform["boundsHeight"]), (1920.0, 1080.0))

    def test_configure_keeps_the_chosen_game_window(self) -> None:
        calls = []

        class FakeConnection:
            def request(self, name, body=None):
                calls.append((name, body))
                return {"GetRecordStatus": {"outputActive": False},
                        "GetProfileList": {"profiles": ["VoiceChanger"], "currentProfileName": "VoiceChanger"},
                        "GetSceneCollectionList": {"sceneCollections": ["VoiceChanger"],
                                                   "currentSceneCollectionName": "VoiceChanger"},
                        "GetSceneList": {"currentProgramSceneName": "シーン"},
                        "GetInputList": {"inputs": [{"inputName": name} for name in
                                                    (obs_setup.DISPLAY_INPUT, obs_setup.GAME_INPUT, obs_setup.VOICE_INPUT)]},
                        "GetSceneItemList": {"sceneItems": [{"sourceName": obs_setup.GAME_INPUT, "sceneItemId": 2}]},
                        "GetInputPropertiesListPropertyItems": {"propertyItems": [
                            {"itemName": "CABLE Output (VB-Audio Virtual Cable)", "itemValue": "{cable}"}]},
                        }.get(name, {})

        original_sleep = obs_setup.time.sleep
        obs_setup.time.sleep = lambda seconds: None
        try:
            obs_setup.configure(FakeConnection(), -110)
        finally:
            obs_setup.time.sleep = original_sleep
        game_updates = [body for name, body in calls
                        if name == "SetInputSettings" and body["inputName"] == obs_setup.GAME_INPUT]
        self.assertEqual(game_updates, [{"inputName": obs_setup.GAME_INPUT, "inputSettings": {"capture_audio": True}}])
        self.assertIn(("SetSceneItemTransform", {"sceneName": "シーン", "sceneItemId": 2,
                                                 "sceneItemTransform": obs_setup.fit_transform()}), calls)


if __name__ == "__main__":
    unittest.main()
