# Offline build of the original SCALE PBL tracer tendency against four stub modules, and the
# library the oracle of `pbl_tracer_diffusion_1d_column` links (README.md §Oracle library).
# `SCALE_ROOT` names a tree of scale-met/scale-dev at the commit README.md states; it is read only.
#   make SCALE_ROOT=<tree> [BUILD=<dir>] lib      the archive the oracle links
#   make SCALE_ROOT=<tree> [BUILD=<dir>]          the archive, the adapter and the offline driver
ifndef SCALE_ROOT
$(error SCALE_ROOT is not set: pass the scale-dev tree, see README.md)
endif
BUILD ?= build
FC := gfortran
# gfortran's module-directory flag; ifort and ifx take MODFLAG="-module " and FFLAGS without
# -ffree-line-length-none.
MODFLAG ?= -J
FFLAGS ?= -O2 -cpp -ffree-line-length-none
SELF := $(dir $(lastword $(MAKEFILE_LIST)))
# The stubs are the Kessler measurement's: the original needs no other.
STUBS := $(SELF)../kessler_measurement/stubs
NODE := $(SELF)../../../spec/component/pbl/diffusion/pbl_tracer_diffusion_1d_column

COMMON := $(SCALE_ROOT)/scalelib/src/common
PBL := $(SCALE_ROOT)/scalelib/src/atmosphere/physics/pbl

# Link order is the `use` order.
LIB_SOURCES := \
  $(COMMON)/scale_precision.F90 \
  $(STUBS)/scale_io.F90 \
  $(STUBS)/scale_prc.F90 \
  $(STUBS)/scale_prof.F90 \
  $(STUBS)/scale_file_history.F90 \
  $(COMMON)/scale_const.F90 \
  $(COMMON)/scale_matrix.F90 \
  $(PBL)/scale_atmos_phy_bl_common.F90
SOURCES := $(LIB_SOURCES) $(NODE)/oracle/adapter.f90 $(SELF)driver.F90

objname = $(BUILD)/$(basename $(notdir $(1))).o
LIB_OBJECTS := $(foreach s,$(LIB_SOURCES),$(call objname,$(s)))
OBJECTS := $(foreach s,$(SOURCES),$(call objname,$(s)))
LIB_NAME := scale_bl_oracle

$(BUILD)/driver: $(OBJECTS)
	$(FC) $(FFLAGS) -o $@ $(OBJECTS)

.PHONY: lib
lib: $(BUILD)/lib$(LIB_NAME).a

$(BUILD)/lib$(LIB_NAME).a: $(LIB_OBJECTS)
	rm -f $@
	ar rcs $@ $(LIB_OBJECTS)

# One serial rule per source: each object needs every module before it in `SOURCES`.
define COMPILE_RULE
$(call objname,$(1)): $(1) $(2) | $(BUILD)
	$(FC) $(FFLAGS) -I$(SCALE_ROOT)/scalelib/include $(MODFLAG)$(BUILD) -I$(BUILD) -c $(1) -o $$@
endef
PREVIOUS :=
$(foreach src,$(SOURCES),$(eval $(call COMPILE_RULE,$(src),$(PREVIOUS)))$(eval PREVIOUS += $(call objname,$(src))))

$(BUILD):
	mkdir -p $@

.PHONY: clean
clean:
	rm -rf $(BUILD)
