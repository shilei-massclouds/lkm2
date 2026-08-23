macro_rules! load_global_pointer {
    () => {
        concat!(
            ".option push\n",
            ".option norelax\n",
            "la gp, __global_pointer$\n",
            ".option pop",
        )
    };
}

pub(crate) use load_global_pointer;
