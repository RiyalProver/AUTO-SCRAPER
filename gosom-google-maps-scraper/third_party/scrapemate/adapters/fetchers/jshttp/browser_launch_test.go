package jshttp

import (
	"slices"
	"testing"
)

func TestBrowserLaunchArgsDisableImagesWithoutRouting(t *testing.T) {
	const imageFlag = "--blink-settings=imagesEnabled=false"

	if args := browserLaunchArgs(true); !slices.Contains(args, imageFlag) {
		t.Fatalf("browserLaunchArgs(true) does not contain %q", imageFlag)
	}

	if args := browserLaunchArgs(false); slices.Contains(args, imageFlag) {
		t.Fatalf("browserLaunchArgs(false) unexpectedly contains %q", imageFlag)
	}
}
