CPSBUILD ?= cpsbuild
REPOSITORIES := core extra experimental

.PHONY: all core-r extra-r experimental-r verify index

all: core-r extra-r experimental-r

define build_repository
	@set -eu; \
	for recipe in $(1)/*.cpsb; do \
		[ -d "$$recipe" ] || continue; \
		$(CPSBUILD) build --output-dir "$(1)" "$$recipe"; \
	done
endef

core-r:
	$(call build_repository,core)

extra-r:
	$(call build_repository,extra)

experimental-r:
	$(call build_repository,experimental)

verify:
	@set -eu; \
	for repository in $(REPOSITORIES); do \
		for package in "$$repository"/*.clos; do \
			[ -f "$$package" ] || continue; \
			$(CPSBUILD) verify "$$package"; \
		done; \
	done

index:
	@set -eu; \
	for repository in $(REPOSITORIES); do \
		$(CPSBUILD) repo-index \
			--output "$$repository/Packages.parquet" \
			"$$repository"; \
	done
