import sys, pathlib, hashlib, json, unittest
sys.path.insert(0,str(pathlib.Path(__file__).parent))
import test_archive_server as support
from test_archive_server import running_server, manifest_for

class StreamingArchiveTest(unittest.TestCase):
    setUp=support.PlatformIntegrationTest.setUp
    request=support.PlatformIntegrationTest.request
    chunk=support.PlatformIntegrationTest.chunk
    def test_sealed_annotations_append_without_changing_observations(self):
        import copy
        original=manifest_for({"events.jsonl":b'{"type":"navigation_end"}\n'})
        original.update({"artifacts_count":0,"type_counts":{},"capture_complete":True})
        with running_server(self.store) as server:
            self.request(server,"PUT","/api/v1/sessions/nav-test",original)
            self.chunk(server,original["files"][0],b'{"type":"navigation_end"}\n')
            self.request(server,"POST","/api/v1/sessions/nav-test/complete",{})
            updated=copy.deepcopy(original);body=b'{"revision":1,"arrival_confirmation":"arrived"}'
            path="artifacts/experiment_annotation/revision-00000001-test.json"
            entry={"path":path,"size":len(body),"sha256":hashlib.sha256(body).hexdigest(),"type":"experiment_annotation"}
            updated["files"].append(entry);updated.update({"artifacts_count":1,"type_counts":{"experiment_annotation":1},"experiment_annotation_revision":1})
            invalid=copy.deepcopy(updated);invalid["ended_at_ms"]+=10
            self.assertEqual(409,self.request(server,"PUT","/api/v1/sessions/nav-test",invalid)[0])
            self.assertEqual(200,self.request(server,"PUT","/api/v1/sessions/nav-test",updated)[0])
            self.assertEqual("uploading",self.store.status("nav-test")["state"])
            self.assertTrue(self.store.status("nav-test")["files"][0]["complete"])
            self.chunk(server,entry,body)
            self.assertEqual(200,self.request(server,"POST","/api/v1/sessions/nav-test/complete",{})[0])
            self.assertEqual(json.loads(body),self.request(server,"GET","/api/v1/sessions/nav-test/files/"+path)[1])
            invalid=copy.deepcopy(updated);invalid["files"][0]["size"]+=1
            self.assertEqual(409,self.request(server,"PUT","/api/v1/sessions/nav-test",invalid)[0])
    def test_active_session_grows_and_seals_without_reupload(self):
        first=b'{"type":"pose","elapsed_time_ns":1000000}\n'
        final=b'{"type":"distance_summary","elapsed_time_ns":2000000}\n'
        initial=manifest_for({"events-00000.jsonl":first},state="recording")
        with running_server(self.store) as server:
            self.assertEqual(200,self.request(server,"PUT","/api/v1/sessions/nav-test",initial)[0])
            self.assertEqual(409,self.request(server,"POST","/api/v1/sessions/nav-test/complete",{})[0])
            self.assertTrue(self.chunk(server,initial["files"][0],first)[1]["complete"])
            sealed=manifest_for({"events-00000.jsonl":first,"events-00001.jsonl":final})
            self.assertEqual(200,self.request(server,"PUT","/api/v1/sessions/nav-test",sealed)[0])
            progress=self.request(server,"GET","/api/v1/sessions/nav-test/upload-status")[1]
            self.assertTrue(progress["files"][0]["complete"])
            self.assertTrue(self.chunk(server,sealed["files"][1],final)[1]["complete"])
            events=self.request(server,"GET","/api/v1/sessions/nav-test/events?offset=0&limit=10")[1]["events"]
            self.assertEqual(["pose","distance_summary"],[e["type"] for e in events])
            window=self.request(server,"GET","/api/v1/sessions/nav-test/events?since_ms=2&until_ms=2")[1]["events"]
            self.assertEqual(["distance_summary"],[e["type"] for e in window])
            self.assertEqual(200,self.request(server,"POST","/api/v1/sessions/nav-test/complete",{})[0])

    def test_active_session_cannot_redefine_published_bytes(self):
        original=manifest_for({"a.bin":b"one"},state="recording")
        changed=manifest_for({"a.bin":b"two"},state="recording")
        with running_server(self.store) as server:
            self.assertEqual(200,self.request(server,"PUT","/api/v1/sessions/nav-test",original)[0])
            self.assertEqual(409,self.request(server,"PUT","/api/v1/sessions/nav-test",changed)[0])

    def test_video_range_returns_exact_requested_bytes(self):
        import http.client
        data=bytes(range(256))*50
        manifest=manifest_for({"video.mp4":data})
        with running_server(self.store) as server:
            self.request(server,"PUT","/api/v1/sessions/nav-test",manifest)
            self.chunk(server,manifest["files"][0],data)
            c=http.client.HTTPConnection("127.0.0.1",server.server_port,timeout=10)
            try:
                c.request("GET","/api/v1/sessions/nav-test/files/video.mp4",headers={"Range":"bytes=100-999"})
                r=c.getresponse();self.assertEqual(206,r.status);self.assertEqual("bytes 100-999/12800",r.getheader("Content-Range"));self.assertEqual(data[100:1000],r.read())
            finally:c.close()
