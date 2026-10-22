(function ($) {
    "use strict";

    function connect(element, retries) {
        const $element = $(element);
        const select2 = $element.data("select2");
        if (!select2) {
            if (retries > 0) {
                window.setTimeout(function () { connect(element, retries - 1); }, 50);
            }
            return;
        }

        const ajax = select2.options.options.ajax;
        if (!ajax || ajax._dependentAutocomplete) {
            return;
        }
        const originalData = ajax.data;
        ajax.data = function (params) {
            const data = originalData(params);
            ["", "-system", "-organization"].forEach(function (suffix) {
                const fieldSelector = $element.attr("data-forward" + suffix + "-field");
                const parameter = $element.attr("data-forward" + suffix + "-param");
                if (fieldSelector && parameter) {
                    data[parameter] = $(fieldSelector).val() || "";
                }
            });
            const systemValue = $element.attr("data-forward-system-value");
            const organizationValue = $element.attr("data-forward-organization-value");
            if (systemValue !== undefined) {
                data.system = systemValue;
            }
            if (organizationValue !== undefined) {
                data.organization = organizationValue;
            }
            return data;
        };
        ajax._dependentAutocomplete = true;
        ["", "-system", "-organization"].forEach(function (suffix) {
            const fieldSelector = $element.attr("data-forward" + suffix + "-field");
            if (fieldSelector) {
                $(fieldSelector).on("change.dependentAutocomplete", function () {
                    $element.val(null).trigger("change");
                });
            }
        });
    }

    function connectAll(root) {
        $(root).find(".admin-autocomplete[data-forward-field]").each(function () {
            connect(this, 20);
        });
    }

    $(function () { connectAll(document); });
    $(document).on("formset:added", function (_event, row) { connectAll(row); });
})(django.jQuery);
