package jshttp

import "testing"

func TestShouldBlockResourceType(t *testing.T) {
	for _, resourceType := range []string{"image", "font", "media", "stylesheet"} {
		if !shouldBlockResourceType(resourceType) {
			t.Errorf("shouldBlockResourceType(%q) = false", resourceType)
		}
	}
	for _, resourceType := range []string{"document", "script", "xhr", "fetch", "text"} {
		if shouldBlockResourceType(resourceType) {
			t.Errorf("shouldBlockResourceType(%q) = true", resourceType)
		}
	}
}
